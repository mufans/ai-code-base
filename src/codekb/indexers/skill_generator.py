"""Skill generation: create actionable task-level skills for agents."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Optional

from codekb.core.config import CodekbYamlConfig
from codekb.storage.doc_store import DocStore
from codekb.storage.sqlite_store import SqliteStore


class SkillGenerator:
    """Generate and manage skills (structured prompts for agents)."""

    def __init__(self, store: SqliteStore, doc_store: DocStore, config: CodekbYamlConfig):
        self.store = store
        self.doc_store = doc_store
        self.config = config

    async def generate_skills(self, repo_name: str, llm_client=None,
                              repo_module: str = "") -> list[dict]:
        """Generate skills for a repo based on structure + docs.

        Skills are task-level actionable prompts like:
        - add-cache-to-service
        - create-api-endpoint
        - add-database-migration
        """
        symbols = self.store.get_symbols(repo_name, repo_module=repo_module or None)
        calls = self.store.get_calls_from(repo_name, "")
        imports = self.store.get_imports(repo_name)

        if not symbols:
            return []

        # Analyze patterns to determine skill candidates
        skill_candidates = self._identify_skill_candidates(repo_name, symbols, imports)

        generated = []
        for candidate in skill_candidates:
            if llm_client:
                skill_content = await self._generate_skill_with_llm(
                    repo_name, candidate, symbols, llm_client
                )
            else:
                skill_content = self._generate_skill_template(
                    repo_name, candidate, symbols
                )

            if skill_content:
                # Auto-verify
                verification = self._auto_verify_skill(repo_name, skill_content)
                # Add YAML frontmatter (prepend to body, preserving body content)
                frontmatter = self._add_skill_frontmatter(candidate, verification)
                skill_content = frontmatter + skill_content
                self.doc_store.write_skill(repo_name, candidate["name"], skill_content,
                                           repo_module=repo_module)

                generated.append({
                    "name": candidate["name"],
                    "confidence": candidate.get("confidence", 0.5),
                    "status": verification["status"],
                    "verified_steps": len(verification["verified"]),
                    "unverified_steps": len(verification["unverified"]),
                })

        return generated

    def _identify_skill_candidates(
        self, repo_name: str, symbols: list, imports: list
    ) -> list[dict]:
        """Identify potential skills based on code patterns."""
        candidates = []
        symbol_names = {s.name for s in symbols}
        import_modules = {i.module for i in imports}
        file_paths = {s.file_path for s in symbols}

        # Detect common patterns
        # Pattern 1: Web/API framework
        web_indicators = {"flask", "fastapi", "express", "django", "starlette", "koa"}
        if web_indicators.intersection(import_modules):
            candidates.append({
                "name": "create-api-endpoint",
                "description": "How to create a new API endpoint",
                "pattern_type": "api",
                "confidence": 0.8,
            })

        # Pattern 2: Database/ORM
        db_indicators = {"sqlalchemy", "django.db", "mongoose", "prisma", "sequelize", "peewee"}
        if db_indicators.intersection(import_modules):
            candidates.append({
                "name": "add-database-migration",
                "description": "How to add a database migration",
                "pattern_type": "database",
                "confidence": 0.7,
            })

        # Pattern 3: Caching
        cache_indicators = {"redis", "cache", "lru_cache", "cachetools", "memcached"}
        if cache_indicators.intersection(import_modules):
            candidates.append({
                "name": "add-caching-to-service",
                "description": "How to add caching to a service",
                "pattern_type": "cache",
                "confidence": 0.7,
            })

        # Pattern 4: Testing
        test_indicators = {"pytest", "unittest", "mocha", "jest", "vitest"}
        if test_indicators.intersection(import_modules):
            candidates.append({
                "name": "add-test",
                "description": "How to add a new test",
                "pattern_type": "testing",
                "confidence": 0.75,
            })

        # Pattern 5: Configuration
        config_names = {s.name for s in symbols if "config" in s.name.lower() or "setting" in s.name.lower()}
        if config_names:
            candidates.append({
                "name": "add-configuration",
                "description": "How to add a new configuration option",
                "pattern_type": "config",
                "confidence": 0.6,
            })

        # If no patterns detected, generate a generic skill
        if not candidates:
            candidates.append({
                "name": "understand-project-structure",
                "description": "How to understand the project structure and conventions",
                "pattern_type": "generic",
                "confidence": 0.5,
            })

        return candidates

    def _generate_skill_template(
        self, repo_name: str, candidate: dict, symbols: list
    ) -> str:
        """Generate a skill template from structure data (no LLM)."""
        name = candidate["name"]
        pattern_type = candidate["pattern_type"]

        # Collect relevant symbols for this skill type
        relevant_symbols = self._get_relevant_symbols(pattern_type, symbols)

        lines = [
            f"# {name.replace('-', ' ').title()}",
            "",
            f"## When to Use",
            f"",
            f"Use this skill when you need to {candidate['description'].lower()}.",
            "",
            f"## Steps",
            "",
        ]

        # Add steps based on available symbols
        for i, sym in enumerate(relevant_symbols[:10], 1):
            lines.append(f"{i}. Reference `{sym.name}` in `{sym.file_path}`")
            if sym.signature:
                lines.append(f"   - Signature: `{sym.signature}`")

        lines.extend([
            "",
            "## Gotchas",
            "",
            "- Follow the existing code conventions in this project",
            "- Check imports match the project's import style",
            "",
            "## Code Example",
            "",
            "```python",
            "# Follow patterns from existing code in this project",
            "```",
            "",
        ])

        return "\n".join(lines)

    async def _generate_skill_with_llm(
        self, repo_name: str, candidate: dict, symbols: list, llm_client
    ) -> Optional[str]:
        """Generate a skill using LLM."""
        relevant_symbols = self._get_relevant_symbols(candidate["pattern_type"], symbols)
        symbol_info = "\n".join(
            f"  {s.kind} {s.name} ({s.file_path}:{s.start_line}): {s.signature}"
            for s in relevant_symbols[:20]
        )

        prompt = f"""Generate an actionable skill document for: {candidate['name']}

Description: {candidate['description']}

This skill should tell an AI agent HOW to {candidate['description'].lower()} in this specific project.
Include:
1. When to use this skill (trigger conditions)
2. Step-by-step instructions with real code templates
3. Gotchas and conventions specific to this project

Use ONLY real symbol names and file paths from the data below:

## Relevant Code Structure:
{symbol_info}

Output in Markdown format. Use backticks for code references."""

        try:
            import litellm
            provider_config = self.config.llm_providers.get(
                self.config.assignments.doc_generation, {}
            )
            model = "gpt-4o-mini"
            if provider_config:
                model = provider_config.model or "gpt-4o-mini"

            # Build kwargs for litellm, using llm_client dict if available
            kwargs = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
            }
            if isinstance(llm_client, dict):
                if llm_client.get("api_base"):
                    kwargs["api_base"] = llm_client["api_base"]
                if llm_client.get("api_key"):
                    kwargs["api_key"] = llm_client["api_key"]

            response = await litellm.acompletion(**kwargs)
            content = response.choices[0].message.content
            if content.startswith("```"):
                content = re.sub(r'^```\w*\n?', '', content)
                content = re.sub(r'\n?```$', '', content)
            return content.strip()
        except Exception:
            return self._generate_skill_template(repo_name, candidate, symbols)

    def _get_relevant_symbols(self, pattern_type: str, symbols: list) -> list:
        """Get symbols relevant to a skill pattern type."""
        if pattern_type == "api":
            return [s for s in symbols if any(kw in s.name.lower() for kw in ["route", "view", "endpoint", "handler", "controller", "app"])]
        elif pattern_type == "database":
            return [s for s in symbols if any(kw in s.name.lower() for kw in ["model", "migration", "schema", "table", "db"])]
        elif pattern_type == "cache":
            return [s for s in symbols if any(kw in s.name.lower() for kw in ["cache", "redis", "memoize"])]
        elif pattern_type == "testing":
            return [s for s in symbols if any(kw in s.name.lower() for kw in ["test", "spec", "mock"])]
        elif pattern_type == "config":
            return [s for s in symbols if any(kw in s.name.lower() for kw in ["config", "setting", "env"])]
        return symbols[:20]

    def _auto_verify_skill(self, repo_name: str, content: str) -> dict:
        """Auto-verify a skill against structure.db.

        Checks:
        - Import paths exist
        - Class names found in structure index
        - File references match actual files
        """
        symbols = self.store.get_symbols(repo_name)
        known_names = {s.name for s in symbols}
        known_files = {s.file_path for s in symbols}

        verified = []
        unverified = []

        # Check backtick-quoted names
        referenced = set(re.findall(r'`([A-Za-z_][A-Za-z0-9_]*)`', content))
        for name in referenced:
            if name in known_names:
                verified.append(f"Symbol '{name}' found in structure index")
            elif len(name) > 3 and not name.isupper():  # Skip constants
                unverified.append(f"Symbol '{name}' not found in structure index")

        # Check file references
        file_refs = set(re.findall(r'`([^`]*\.\w+)`', content))
        for ref in file_refs:
            if ref in known_files:
                verified.append(f"File path '{ref}' exists in file tree")

        # Determine status
        if not unverified and verified:
            status = "verified"
        elif unverified and verified:
            status = "draft"
        else:
            status = "draft"

        return {
            "status": status,
            "verified": verified,
            "unverified": unverified,
        }

    def _add_skill_frontmatter(self, candidate: dict, verification: dict) -> str:
        """Add YAML frontmatter to a skill."""
        now = datetime.now(timezone.utc).isoformat()
        frontmatter = f"""---
name: {candidate['name']}
confidence: {candidate.get('confidence', 0.5)}
status: {verification['status']}
verified_steps:
"""
        for step in verification["verified"][:10]:
            frontmatter += f'  - "{step}"\n'

        frontmatter += "unverified_steps:\n"
        for step in verification["unverified"][:10]:
            frontmatter += f'  - "{step}"\n'

        frontmatter += f"""last_verified_at: {now}
---

"""
        return frontmatter

    def list_skills(self, repo_name: str) -> list[dict]:
        """List all skills with status."""
        skills = self.doc_store.list_skills(repo_name)
        result = []
        for skill_name in skills:
            content = self.doc_store.read_skill(repo_name, skill_name)
            if content is None:
                continue

            # Parse frontmatter
            meta = self._parse_frontmatter(content)
            result.append({
                "name": skill_name,
                "status": meta.get("status", "unknown"),
                "confidence": meta.get("confidence", 0),
            })
        return result

    def verify_skills(self, repo_name: str) -> list[dict]:
        """Re-verify all skills against current code."""
        skills = self.doc_store.list_skills(repo_name)
        results = []

        for skill_name in skills:
            content = self.doc_store.read_skill(repo_name, skill_name)
            if content is None:
                continue

            # Strip frontmatter for verification
            body = self._strip_frontmatter(content)
            verification = self._auto_verify_skill(repo_name, body)

            # Re-read candidate info from frontmatter
            meta = self._parse_frontmatter(content)
            candidate = {
                "name": skill_name,
                "confidence": meta.get("confidence", 0.5),
            }

            # Update skill with new frontmatter
            updated = self._add_skill_frontmatter(candidate, verification) + body
            self.doc_store.write_skill(repo_name, skill_name, updated)

            results.append({
                "name": skill_name,
                "status": verification["status"],
                "verified": len(verification["verified"]),
                "unverified": len(verification["unverified"]),
            })

        return results

    def review_skill(self, repo_name: str, skill_name: str, approve: bool = True) -> dict:
        """Review and approve/reject a draft skill."""
        content = self.doc_store.read_skill(repo_name, skill_name)
        if content is None:
            return {"error": "Skill not found"}

        meta = self._parse_frontmatter(content)
        body = self._strip_frontmatter(content)

        if approve:
            meta["status"] = "verified"
        else:
            meta["status"] = "deprecated"

        # Rebuild frontmatter
        verification = {
            "status": meta["status"],
            "verified": meta.get("verified_steps", []),
            "unverified": meta.get("unverified_steps", []),
        }
        candidate = {"name": skill_name, "confidence": meta.get("confidence", 0.5)}
        updated = self._add_skill_frontmatter(candidate, verification) + body
        self.doc_store.write_skill(repo_name, skill_name, updated)

        return {"name": skill_name, "status": meta["status"]}

    def _parse_frontmatter(self, content: str) -> dict:
        """Parse YAML frontmatter from content."""
        if not content.startswith("---"):
            return {}

        end = content.find("---", 3)
        if end == -1:
            return {}

        yaml_text = content[3:end].strip()
        meta = {}

        for line in yaml_text.split("\n"):
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip().strip('"')
                if value:
                    meta[key] = value

        return meta

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter."""
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                return content[end + 3:].lstrip("\n")
        return content
