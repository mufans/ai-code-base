"""Guide generator: orchestrates SQLite queries + LLM generation for 4 guide tools."""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from codekb.mcp.guide_cache import GuideCache
from codekb.storage.sqlite_store import SqliteStore

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = {
    "component_guide": 12000,
    "usage_examples": 8000,
    "recommend": 6000,
    "api_guide": 15000,
}


def _truncate_code(code: str, max_len: int = 1500) -> str:
    if len(code) <= max_len:
        return code
    return code[:max_len] + "\n... (truncated)"


def _truncate_result(result: dict, tool_type: str) -> dict:
    max_chars = MAX_OUTPUT_CHARS.get(tool_type, 10000)
    serialized = json.dumps(result, ensure_ascii=False)
    if len(serialized) <= max_chars:
        return result
    for key in ("usage_example", "example", "source", "code"):
        if key in result and isinstance(result[key], str) and len(result[key]) > 500:
            result[key] = _truncate_code(result[key], 500)
    for ex in result.get("examples", []):
        if "code" in ex and isinstance(ex["code"], str):
            ex["code"] = _truncate_code(ex["code"], 1000)
    return result


async def _call_llm(prompt: str, llm_client: dict) -> Optional[str]:
    try:
        import litellm
        kwargs = {
            "model": llm_client.get("model", "gpt-4o-mini"),
            "messages": [{"role": "user", "content": prompt}],
        }
        if llm_client.get("api_base"):
            kwargs["api_base"] = llm_client["api_base"]
        if llm_client.get("api_key"):
            kwargs["api_key"] = llm_client["api_key"]
        response = await litellm.acompletion(**kwargs)
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return None


class GuideGenerator:
    """Core logic for the 4 guide query tools."""

    def __init__(self, store: SqliteStore, cache: GuideCache):
        self.store = store
        self.cache = cache

    async def get_component_guide(
        self,
        repo_name: str,
        symbol_name: str,
        module: Optional[str] = None,
        llm_client: Optional[dict] = None,
    ) -> dict:
        tool_type = "component_guide"

        cached = self.cache.get(repo_name, tool_type, symbol_name, module=module or "")
        if cached is not None:
            return cached

        symbols = self.store.get_symbol_by_name(repo_name, symbol_name, repo_module=module)
        if not symbols:
            return {"error": f"Symbol not found: {symbol_name}"}

        main_symbol = symbols[0]
        children = [s for s in symbols[1:] if s.parent == symbol_name] if len(symbols) > 1 else []

        calls = self.store.get_calls_to(repo_name, symbol_name, repo_module=module)
        caller_files = list(set(c.caller_file for c in calls))

        imports = self.store.get_imports(repo_name, repo_module=module)
        importer_files = [
            imp.file_path for imp in imports
            if symbol_name in (imp.imported_names or "")
        ]

        all_caller_files = list(set(caller_files + importer_files))
        caller_snippets = []
        for f in all_caller_files[:5]:
            caller_symbols = self.store.get_symbols(repo_name, file_path=f)
            for cs in caller_symbols:
                if symbol_name in (cs.source or ""):
                    caller_snippets.append({
                        "file": cs.file_path,
                        "name": cs.name,
                        "line_range": f"{cs.start_line}-{cs.end_line}",
                        "code": _truncate_code(cs.source or "", 800),
                    })
                    if len(caller_snippets) >= 3:
                        break
            if len(caller_snippets) >= 3:
                break

        related = list(set(
            s.name for s in self.store.get_symbols(repo_name, file_path=main_symbol.file_path)
            if s.name != symbol_name and not s.parent
        ))[:5]

        properties = []
        methods = []
        for child in children:
            entry = {"name": child.name, "signature": child.signature}
            if child.kind in ("method", "function"):
                methods.append(entry)
            else:
                properties.append(entry)

        description = None
        usage_example = None
        if llm_client is not None:
            prompt = self._build_component_guide_prompt(
                symbol_name, main_symbol, properties, methods, caller_snippets
            )
            response = await _call_llm(prompt, llm_client)
            if response:
                try:
                    cleaned = re.sub(r'^```\w*\n?', '', response)
                    cleaned = re.sub(r'\n?```$', '', cleaned).strip()
                    llm_result = json.loads(cleaned)
                    description = llm_result.get("description")
                    usage_example = llm_result.get("usage_example")
                except json.JSONDecodeError:
                    description = response[:500]

        result = {
            "symbol_name": symbol_name,
            "type": main_symbol.kind,
            "language": main_symbol.language,
            "file": main_symbol.file_path,
            "module": main_symbol.repo_module,
            "description": description,
            "properties": properties,
            "methods": methods,
            "usage_example": usage_example,
            "related_symbols": related,
        }

        result = _truncate_result(result, tool_type)
        self.cache.set(repo_name, tool_type, symbol_name, result, module=module or "")
        return result

    def _build_component_guide_prompt(self, name, symbol, properties, methods, snippets):
        props_str = "\n".join(f"  - {p['name']}: {p['signature']}" for p in properties) or "  (none)"
        methods_str = "\n".join(f"  - {m['name']}: {m['signature']}" for m in methods) or "  (none)"
        snippets_str = "\n".join(
            f"  File: {s['file']}\n  {s['code']}" for s in snippets
        ) or "  (no usage found)"

        return f"""Analyze this code component and generate a usage guide.

Component: {name} ({symbol.kind})
File: {symbol.file_path}
Source:
{symbol.source or ""}

Properties:
{props_str}

Methods:
{methods_str}

Usage in other files:
{snippets_str}

Return ONLY a JSON object with these fields:
- "description": A concise description of what this component does and when to use it (1-2 sentences)
- "usage_example": A minimal code example showing how to use this component (as a string)
"""

    async def get_usage_examples(
        self,
        repo_name: str,
        symbol_name: str,
        module: Optional[str] = None,
        top_k: int = 5,
        llm_client: Optional[dict] = None,
    ) -> dict:
        tool_type = "usage_examples"

        cached = self.cache.get(repo_name, tool_type, symbol_name, module=module or "")
        if cached is not None:
            return cached

        def_symbols = self.store.get_symbol_by_name(repo_name, symbol_name, repo_module=module)
        def_files = set(s.file_path for s in def_symbols)

        calls = self.store.get_calls_to(repo_name, symbol_name, repo_module=module)
        caller_files = set(c.caller_file for c in calls)

        imports = self.store.get_imports(repo_name, repo_module=module)
        importer_files = set(
            imp.file_path for imp in imports
            if symbol_name in (imp.imported_names or "")
        )

        candidate_files = (caller_files | importer_files) - def_files

        examples = []
        for f in list(candidate_files)[:10]:
            file_symbols = self.store.get_symbols(repo_name, file_path=f)
            for s in file_symbols:
                if symbol_name in (s.source or ""):
                    context = None
                    if llm_client is not None and len(examples) < top_k:
                        context = await self._generate_context(symbol_name, s, llm_client)
                    examples.append({
                        "file": s.file_path,
                        "line_range": f"{s.start_line}-{s.end_line}",
                        "code": _truncate_code(s.source or "", 800),
                        "context": context,
                    })
                    if len(examples) >= top_k:
                        break
            if len(examples) >= top_k:
                break

        result = {
            "symbol_name": symbol_name,
            "examples": examples,
        }
        result = _truncate_result(result, tool_type)
        self.cache.set(repo_name, tool_type, symbol_name, result, module=module or "")
        return result

    async def _generate_context(self, symbol_name, symbol, llm_client):
        prompt = f"""In one short sentence, describe how {symbol_name} is used in this code:

File: {symbol.file_path}
Code: {(symbol.source or "")[:1000]}

Context (one sentence):"""
        response = await _call_llm(prompt, llm_client)
        return response.strip() if response else None

    async def recommend_component(
        self,
        repo_name: str,
        requirement: str,
        module: Optional[str] = None,
        top_k: int = 3,
        llm_client: Optional[dict] = None,
    ) -> dict:
        tool_type = "recommend"

        cached = self.cache.get(repo_name, tool_type, requirement, module=module or "")
        if cached is not None:
            return cached

        symbols = self.store.get_symbols(repo_name, repo_module=module)
        symbol_list = []
        seen = set()
        for s in symbols:
            if not s.parent and s.name not in seen:
                seen.add(s.name)
                symbol_list.append(f"{s.name} ({s.kind}) - {s.repo_module}")
        symbol_list_str = "\n".join(f"{i+1}. {line}" for i, line in enumerate(symbol_list[:200]))

        if llm_client is None:
            return {
                "requirement": requirement,
                "recommendations": [],
                "error": "LLM client required for recommend_component",
            }

        prompt = f"""User requirement: {requirement}

Available components in {repo_name}:
{symbol_list_str}

Recommend the top {top_k} components that best match the requirement.
Return ONLY a JSON array, each element with:
- "symbol_name": exact name from the list above
- "relevance_score": 0-1
- "reason": one sentence explaining why it matches
- "brief_usage": one line showing how to use it
"""

        response = await _call_llm(prompt, llm_client)
        recommendations = []
        if response:
            try:
                cleaned = re.sub(r'^```\w*\n?', '', response)
                cleaned = re.sub(r'\n?```$', '', cleaned).strip()
                recommendations = json.loads(cleaned)
                if not isinstance(recommendations, list):
                    recommendations = [recommendations]
            except json.JSONDecodeError:
                logger.error("Failed to parse LLM recommendation response")

        result = {
            "requirement": requirement,
            "recommendations": recommendations[:top_k],
        }
        result = _truncate_result(result, tool_type)
        self.cache.set(repo_name, tool_type, requirement, result, module=module or "")
        return result

    async def get_api_guide(
        self,
        repo_name: str,
        library_name: str,
        module: Optional[str] = None,
        llm_client: Optional[dict] = None,
    ) -> dict:
        tool_type = "api_guide"

        cached = self.cache.get(repo_name, tool_type, library_name, module=module or "")
        if cached is not None:
            return cached

        # Check if library_name matches a module with a README
        readme_content = self._try_read_module_readme(repo_name, library_name)
        if readme_content:
            result = {
                "library_name": library_name,
                "source": "readme",
                "guide": {"description": readme_content},
            }
            self.cache.set(repo_name, tool_type, library_name, result, module=module or "")
            return result

        file_tree = self.store.get_file_tree(repo_name, repo_module=module)
        export_files = [
            f for f in file_tree
            if f.path and (
                f.path.endswith("Index.ets") or
                f.path.endswith("index.ts") or
                f.path.endswith("index.js") or
                f.path.endswith("__init__.py")
            )
        ]

        target_module = module or library_name
        symbols = self.store.get_symbols(repo_name, repo_module=target_module)

        components = []
        seen = set()
        for s in symbols:
            if not s.parent and s.name not in seen and s.kind in (
                "struct", "class", "interface", "enum", "function", "constant"
            ):
                seen.add(s.name)
                components.append({"name": s.name, "kind": s.kind})

        imports = self.store.get_imports(repo_name, repo_module=module)
        import_statements = []
        for imp in imports:
            if library_name in (imp.module or ""):
                import_statements.append(
                    f"import {{ {imp.imported_names} }} from '{imp.module}'"
                )

        guide = None
        if llm_client is not None:
            prompt = self._build_api_guide_prompt(library_name, components, import_statements, export_files)
            response = await _call_llm(prompt, llm_client)
            if response:
                try:
                    cleaned = re.sub(r'^```\w*\n?', '', response)
                    cleaned = re.sub(r'\n?```$', '', cleaned).strip()
                    guide = json.loads(cleaned)
                except json.JSONDecodeError:
                    guide = {"description": response[:500]}

        if guide is None:
            guide = {
                "description": None,
                "import_statement": import_statements[0] if import_statements else None,
                "components": [{"name": c["name"], "brief": None} for c in components[:20]],
                "setup": None,
                "example": None,
            }

        result = {"library_name": library_name, "source": "llm_generated", "guide": guide}
        result = _truncate_result(result, tool_type)
        self.cache.set(repo_name, tool_type, library_name, result, module=module or "")
        return result

    def _try_read_module_readme(self, repo_name: str, library_name: str) -> Optional[str]:
        """Try to read a module's README if library_name matches a module name."""
        from pathlib import Path

        modules_data = self.store.get_repo_modules(repo_name)
        module_path = None
        for mod in modules_data:
            if mod["name"] == library_name:
                module_path = mod.get("path", "")
                break

        if module_path is None:
            return None

        repo = self.store.get_repo(repo_name)
        if repo is None:
            return None

        module_dir = Path(repo.local_path) / module_path
        for name in ["README.md", "README.rst", "README.txt", "README"]:
            readme = module_dir / name
            if readme.exists():
                return readme.read_text(encoding="utf-8", errors="replace")
        return None

    def _build_api_guide_prompt(self, library_name, components, imports, export_files):
        comp_str = "\n".join(f"  - {c['name']} ({c['kind']})" for c in components[:30])
        imports_str = "\n".join(f"  {imp}" for imp in imports[:10]) or "  (none found)"
        files_str = "\n".join(f"  {f.path}" for f in export_files[:5]) or "  (none found)"

        return f"""Generate an API integration guide for the library/module: {library_name}

Exported components:
{comp_str}

Known import statements:
{imports_str}

Export files:
{files_str}

Return ONLY a JSON object with:
- "description": 1-2 sentence description of the library
- "import_statement": the typical import statement
- "components": array of {{"name", "brief"}} for each component (brief = 3-5 words)
- "setup": setup steps as a string (numbered list)
- "example": a complete code example showing how to integrate and use this library
"""
