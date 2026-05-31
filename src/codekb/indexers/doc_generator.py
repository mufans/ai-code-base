"""LLM-powered architecture doc generation with coverage assessment and verification."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from codekb.core.config import CodekbYamlConfig, Settings
from codekb.indexers.embedder import EmbeddingIndexer
from codekb.storage.doc_store import DocStore
from codekb.storage.sqlite_store import SqliteStore


# Coverage dimensions assessed from README
COVERAGE_DIMENSIONS = [
    "overview",
    "quickstart",
    "architecture",
    "core_chain",
    "api_reference",
    "configuration",
    "dependencies",
    "usage_examples",
]

# Quality tags for generated docs
QUALITY_TAGS = ("verified", "partial", "draft", "manual")


class CoverageAssessment:
    """Assess README coverage across 8 dimensions."""

    def __init__(self, store: SqliteStore, doc_store: DocStore):
        self.store = store
        self.doc_store = doc_store

    async def assess(self, repo_name: str, readme_content: str, llm_client=None) -> dict:
        """Assess README coverage and return COVERAGE.json structure.

        If llm_client is None, uses simple heuristic assessment.
        """
        if llm_client is None:
            return self._heuristic_assess(repo_name, readme_content)
        else:
            return await self._llm_assess(repo_name, readme_content, llm_client)

    def _heuristic_assess(self, repo_name: str, readme_content: str) -> dict:
        """Simple heuristic assessment based on content patterns."""
        content_lower = readme_content.lower()
        coverage = {
            "total_dimensions": 8,
            "covered_by_readme": [],
            "generated": [],
            "enriched": [],
            "missing": [],
        }

        # Check for overview
        if any(kw in content_lower for kw in ["# ", "about", "overview", "description"]):
            coverage["covered_by_readme"].append("overview")
        else:
            coverage["missing"].append("overview")

        # Check for quickstart
        if any(kw in content_lower for kw in ["install", "getting started", "quick start", "usage"]):
            coverage["covered_by_readme"].append("quickstart")
        else:
            coverage["missing"].append("quickstart")

        # Check for architecture
        if any(kw in content_lower for kw in ["architecture", "design", "structure", "component"]):
            coverage["covered_by_readme"].append("architecture")
        else:
            coverage["missing"].append("architecture")

        # Check for core chain
        if any(kw in content_lower for kw in ["flow", "pipeline", "workflow", "data flow", "chain"]):
            coverage["covered_by_readme"].append("core_chain")
        else:
            coverage["missing"].append("core_chain")

        # Check for api reference
        if any(kw in content_lower for kw in ["api", "endpoint", "method", "function reference"]):
            coverage["covered_by_readme"].append("api_reference")
        else:
            coverage["missing"].append("api_reference")

        # Check for configuration
        if any(kw in content_lower for kw in ["config", "setting", "environment variable", "env"]):
            coverage["covered_by_readme"].append("configuration")
        else:
            coverage["missing"].append("configuration")

        # Check for dependencies
        if any(kw in content_lower for kw in ["depend", "requirement", "prerequisite", "install"]):
            coverage["covered_by_readme"].append("dependencies")
        else:
            coverage["missing"].append("dependencies")

        # Check for usage examples
        if any(kw in content_lower for kw in ["example", "demo", "sample", "```"]):
            coverage["covered_by_readme"].append("usage_examples")
        else:
            coverage["missing"].append("usage_examples")

        return coverage

    async def _llm_assess(self, repo_name: str, readme_content: str, llm_client) -> dict:
        """LLM-based coverage assessment."""
        symbols = self.store.get_symbols(repo_name)
        symbol_summary = self._build_symbol_summary(symbols)

        prompt = f"""Assess the following README for coverage across these 8 dimensions:
overview, quickstart, architecture, core_chain, api_reference, configuration, dependencies, usage_examples

For each dimension, determine if it is: covered, partial, or missing in the README.

Return ONLY a JSON object with keys: covered_by_readme (list), partial (list), missing (list).

Project structure summary:
{symbol_summary[:2000]}

README:
{readme_content[:4000]}
"""
        try:
            import litellm
            response = await litellm.acompletion(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            result = json.loads(response.choices[0].message.content)
            coverage = {
                "total_dimensions": 8,
                "covered_by_readme": result.get("covered_by_readme", []),
                "generated": [],
                "enriched": result.get("partial", []),
                "missing": result.get("missing", []),
            }
            return coverage
        except Exception:
            return self._heuristic_assess(repo_name, readme_content)

    def _build_symbol_summary(self, symbols) -> str:
        """Build a concise summary of project symbols for LLM context."""
        lines = []
        current_file = ""
        for sym in symbols:
            if sym.file_path != current_file:
                current_file = sym.file_path
                lines.append(f"\n{current_file}:")
            indent = "  " if sym.parent else "  "
            lines.append(f"{indent}{sym.kind} {sym.name} {sym.signature}")
        return "\n".join(lines[:200])  # Limit size


class DocGenerator:
    """Generate architecture docs with LLM, verify programmatically."""

    def __init__(self, store: SqliteStore, doc_store: DocStore, config: CodekbYamlConfig):
        self.store = store
        self.doc_store = doc_store
        self.config = config
        self.coverage_assessor = CoverageAssessment(store, doc_store)

    async def generate_docs(self, repo_name: str, llm_client=None) -> dict:
        """Generate missing architecture docs for a repo.

        Pipeline:
        1. Read README → assess coverage
        2. Get structure summary
        3. LLM generates missing docs (or creates basic template)
        4. Programmatic verification
        5. Store coverage + docs
        """
        # Step 1: Get README
        repo = self.store.get_repo(repo_name)
        if repo is None:
            raise ValueError(f"Repo not found: {repo_name}")

        repo_path = Path(repo.local_path)
        readme = self._find_readme(repo_path)
        readme_content = readme.read_text(encoding="utf-8", errors="replace") if readme else ""

        # Step 2: Assess coverage
        coverage = await self.coverage_assessor.assess(repo_name, readme_content, llm_client)

        # Step 3: Get structure summary for context
        symbols = self.store.get_symbols(repo_name)
        calls = self._get_call_summary(repo_name)
        symbol_summary = self.coverage_assessor._build_symbol_summary(symbols)

        # Step 4: Generate missing docs
        generated = []
        for dim in coverage.get("missing", []):
            doc_name = self._dim_to_doc_name(dim)
            if doc_name:
                content = await self._generate_doc(
                    repo_name, dim, doc_name, readme_content, symbol_summary, calls, llm_client
                )
                if content:
                    # Step 5: Verify
                    quality = self._verify_doc(repo_name, content)
                    # Add frontmatter
                    content = self._add_frontmatter(content, quality, dim)
                    self.doc_store.write_doc(repo_name, doc_name, content)
                    coverage["generated"].append(dim)
                    generated.append({"dimension": dim, "doc": doc_name, "quality": quality["tag"]})

        # Also enrich partial dimensions
        for dim in coverage.get("enriched", []):
            doc_name = self._dim_to_doc_name(dim)
            if doc_name:
                content = await self._generate_doc(
                    repo_name, dim, doc_name, readme_content, symbol_summary, calls, llm_client
                )
                if content:
                    quality = self._verify_doc(repo_name, content)
                    content = self._add_frontmatter(content, quality, dim)
                    self.doc_store.write_doc(repo_name, doc_name, content)
                    coverage["enriched"].remove(dim)
                    coverage["generated"].append(dim)
                    generated.append({"dimension": dim, "doc": doc_name, "quality": quality["tag"]})

        # Store coverage
        self.doc_store.write_coverage(repo_name, coverage)

        return {
            "repo": repo_name,
            "coverage": coverage,
            "generated_docs": generated,
        }

    async def _generate_doc(
        self, repo_name: str, dimension: str, doc_name: str,
        readme: str, symbol_summary: str, call_summary: str,
        llm_client=None,
    ) -> Optional[str]:
        """Generate a single doc using LLM or template fallback."""
        if llm_client is None:
            # Fallback: generate template from structure data
            return self._generate_template(repo_name, dimension, symbol_summary, call_summary)

        prompt = self._build_generation_prompt(dimension, readme, symbol_summary, call_summary)

        try:
            import litellm
            provider_config = self.config.llm_providers.get(
                self.config.assignments.doc_generation, {}
            )
            model = "gpt-4o-mini"
            if provider_config:
                model = provider_config.model or "gpt-4o-mini"

            response = await litellm.acompletion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.choices[0].message.content
            # Strip markdown code block if present
            if content.startswith("```"):
                content = re.sub(r'^```\w*\n?', '', content)
                content = re.sub(r'\n?```$', '', content)
            return content.strip()
        except Exception:
            return self._generate_template(repo_name, dimension, symbol_summary, call_summary)

    def _generate_template(
        self, repo_name: str, dimension: str, symbol_summary: str, call_summary: str
    ) -> str:
        """Generate a basic doc template from structure data (no LLM)."""
        templates = {
            "architecture": self._template_architecture,
            "core_chain": self._template_core_chain,
            "api_reference": self._template_api_reference,
            "configuration": self._template_configuration,
            "overview": self._template_overview,
            "quickstart": self._template_quickstart,
            "dependencies": self._template_dependencies,
            "usage_examples": self._template_usage_examples,
        }
        generator = templates.get(dimension)
        if generator:
            return generator(repo_name, symbol_summary, call_summary)
        return f"# {dimension.replace('_', ' ').title()}\n\nGenerated from structure data.\n\n{symbol_summary[:500]}\n"

    def _template_architecture(self, repo_name: str, symbols: str, calls: str) -> str:
        return f"""# Architecture

## Overview

This document describes the architecture of {repo_name}.

## Module Structure

{symbols[:2000]}

## Call Graph Summary

{calls[:1000]}

> This document was auto-generated from tree-sitter structure data and needs review.
"""

    def _template_core_chain(self, repo_name: str, symbols: str, calls: str) -> str:
        # Extract entry point symbols
        lines = ["# Core Chain", "", "## Main Data Flow", ""]
        lines.append("The following describes the primary execution paths:")
        lines.append("")
        lines.append(f"### Call Relationships")
        lines.append(calls[:2000])
        lines.append("")
        lines.append("> Auto-generated from structure data.")
        return "\n".join(lines)

    def _template_api_reference(self, repo_name: str, symbols: str, calls: str) -> str:
        return f"""# API Reference

## Public API

{symbols[:3000]}

> Auto-generated from tree-sitter structure data.
"""

    def _template_configuration(self, repo_name: str, symbols: str, calls: str) -> str:
        return "# Configuration\n\nConfiguration documentation pending.\n\n> Auto-generated placeholder.\n"

    def _template_overview(self, repo_name: str, symbols: str, calls: str) -> str:
        return f"# Overview\n\n{repo_name} - auto-generated overview.\n\n{symbols[:1000]}\n"

    def _template_quickstart(self, repo_name: str, symbols: str, calls: str) -> str:
        return "# Quick Start\n\nGetting started guide pending.\n\n> Auto-generated placeholder.\n"

    def _template_dependencies(self, repo_name: str, symbols: str, calls: str) -> str:
        return "# Dependencies\n\nDependency documentation pending.\n\n> Auto-generated placeholder.\n"

    def _template_usage_examples(self, repo_name: str, symbols: str, calls: str) -> str:
        return "# Usage Examples\n\nUsage examples pending.\n\n> Auto-generated placeholder.\n"

    def _verify_doc(self, repo_name: str, content: str) -> dict:
        """Programmatic verification of generated doc against structure.db.

        Checks:
        - Class/function names mentioned exist in symbol table
        - Returns quality tag and list of verified/unverified claims
        """
        symbols = self.store.get_symbols(repo_name)
        known_names = {s.name for s in symbols}
        known_files = {s.file_path for s in symbols}

        # Extract potential code references from doc
        # Look for backtick-quoted names
        referenced_names = set(re.findall(r'`([A-Za-z_][A-Za-z0-9_]*)`', content))
        # Look for file paths
        referenced_files = set(re.findall(r'`([^`]*\.\w+)`', content))

        verified = []
        unverified = []

        for name in referenced_names:
            if name in known_names:
                verified.append(f"Symbol '{name}' found in structure index")
            elif len(name) > 3:  # Skip short names that are likely not code
                unverified.append(f"Symbol '{name}' not found in structure index")

        for file_ref in referenced_files:
            if file_ref in known_files:
                verified.append(f"File '{file_ref}' found in file tree")
            elif "/" in file_ref or "." in file_ref:
                unverified.append(f"File '{file_ref}' not found in file tree")

        # Determine quality tag
        if not unverified:
            tag = "verified"
        elif len(verified) >= len(unverified):
            tag = "partial"
        else:
            tag = "draft"

        return {
            "tag": tag,
            "verified_steps": verified,
            "unverified_steps": unverified,
        }

    def verify_docs(self, repo_name: str) -> list[dict]:
        """Verify all generated docs for a repo."""
        results = []
        docs = self.doc_store.list_docs(repo_name)

        for doc_name in docs:
            if not doc_name.endswith(".md"):
                continue
            content = self.doc_store.read_doc(repo_name, doc_name)
            if content is None:
                continue

            quality = self._verify_doc(repo_name, content)

            # Update frontmatter
            updated = self._add_frontmatter(content, quality, doc_name)
            self.doc_store.write_doc(repo_name, doc_name, updated)

            results.append({
                "doc": doc_name,
                "quality": quality["tag"],
                "verified": len(quality["verified_steps"]),
                "unverified": len(quality["unverified_steps"]),
                "unverified_claims": quality["unverified_steps"],
            })

        return results

    def _add_frontmatter(self, content: str, quality: dict, dimension: str) -> str:
        """Add YAML frontmatter with quality metadata."""
        existing = self._strip_frontmatter(content)
        frontmatter = f"""---
quality: {quality['tag']}
dimension: {dimension}
generated_at: {datetime.now(timezone.utc).isoformat()}
verified_steps: {json.dumps(quality.get('verified_steps', []))}
unverified_steps: {json.dumps(quality.get('unverified_steps', []))}
---

"""
        return frontmatter + existing

    def _strip_frontmatter(self, content: str) -> str:
        """Remove existing YAML frontmatter."""
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                return content[end + 3:].lstrip("\n")
        return content

    def _dim_to_doc_name(self, dimension: str) -> Optional[str]:
        """Map coverage dimension to doc filename."""
        mapping = {
            "architecture": "ARCHITECTURE.md",
            "core_chain": "CORE_CHAIN.md",
            "api_reference": "API_REFERENCE.md",
            "configuration": "CONFIGURATION.md",
            "overview": None,  # Usually in README
            "quickstart": None,  # Usually in README
            "dependencies": None,  # Usually in README
            "usage_examples": "USAGE.md",
        }
        return mapping.get(dimension)

    def _get_call_summary(self, repo_name: str) -> str:
        """Get a summary of the call graph."""
        symbols = self.store.get_symbols(repo_name)
        lines = []
        for sym in symbols:
            if sym.kind in ("function", "method"):
                calls = self.store.get_calls_from(repo_name, sym.name)
                if calls:
                    callees = [c.callee_name for c in calls[:10]]
                    lines.append(f"  {sym.name} → {', '.join(callees)}")
        return "\n".join(lines[:100])

    def _build_generation_prompt(
        self, dimension: str, readme: str, symbols: str, calls: str
    ) -> str:
        """Build LLM prompt for doc generation."""
        dim_descriptions = {
            "architecture": "an architecture overview document explaining the project structure, modules, and their relationships",
            "core_chain": "a core chain analysis document showing the main data/business flow through the codebase",
            "api_reference": "an API reference documenting all public classes, functions, and their signatures",
            "configuration": "a configuration reference documenting all config options and environment variables",
            "usage_examples": "usage examples showing how to use the project's main features",
        }

        return f"""Generate {dim_descriptions.get(dimension, f'a {dimension} document')} for this project.

Use ONLY the real symbol names, file paths, and relationships from the structure data below.
Do NOT invent code, classes, or functions that don't appear in the data.

## Project Structure (from tree-sitter):
{symbols[:3000]}

## Call Graph:
{calls[:1000]}

## Existing README:
{readme[:2000]}

Output the document in Markdown format. Use real symbol names in backticks.
"""

    def _find_readme(self, repo_path: Path) -> Optional[Path]:
        for name in ["README.md", "README.rst", "README.txt", "README"]:
            path = repo_path / name
            if path.exists():
                return path
        return None
