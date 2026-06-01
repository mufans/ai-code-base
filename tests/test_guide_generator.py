"""Tests for guide generator."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from codekb.retrieval.guide_generator import GuideGenerator
from codekb.mcp.guide_cache import GuideCache
from codekb.storage.sqlite_store import SqliteStore, Symbol, CallRelation, ImportRecord


@pytest.fixture
def store(tmp_path):
    return SqliteStore(tmp_path / "index")


@pytest.fixture
def cache(store):
    return GuideCache(store)


@pytest.fixture
def generator(store, cache):
    return GuideGenerator(store, cache)


def _seed_symbols(store: SqliteStore):
    store.insert_symbols([
        Symbol(
            repo_name="test-repo", file_path="lib/components/Button.ets",
            name="Button", kind="struct",
            signature="struct Button",
            docstring="A reusable button component",
            start_line=1, end_line=30, parent="", language="typescript",
            source='@Component\nstruct Button {\n  @Prop text: string = ""\n  build() { ... }\n}',
        ),
        Symbol(
            repo_name="test-repo", file_path="lib/components/Button.ets",
            name="onClick", kind="method",
            signature="onClick(handler: () => void): void",
            start_line=10, end_line=15, parent="Button", language="typescript",
            source="onClick(handler: () => void): void { this._handler = handler }",
        ),
    ])
    store.insert_symbols([
        Symbol(
            repo_name="test-repo", file_path="pages/HomePage.ets",
            name="HomePage", kind="struct",
            signature="struct HomePage",
            start_line=1, end_line=50, parent="", language="typescript",
            source='Button({ text: "Submit" })',
        ),
    ])
    store.insert_calls([
        CallRelation(
            repo_name="test-repo", caller_file="pages/HomePage.ets",
            caller_name="HomePage", callee_name="Button", callee_file="",
            line_number=20,
        ),
    ])
    store.insert_imports([
        ImportRecord(
            repo_name="test-repo", file_path="pages/HomePage.ets",
            module="@jfzt/components", imported_names='["Button"]',
            line_number=1, is_relative=False,
        ),
    ])


class TestGetComponentGuide:
    async def test_returns_cached_result(self, generator, cache):
        cached = {"symbol_name": "Button", "description": "cached"}
        cache.set("test-repo", "component_guide", "Button", cached)

        result = await generator.get_component_guide("test-repo", "Button")
        assert result["symbol_name"] == "Button"
        assert result["description"] == "cached"

    async def test_symbol_not_found(self, generator):
        result = await generator.get_component_guide("test-repo", "NonExistent")
        assert "error" in result

    async def test_generates_guide_without_llm(self, generator, store):
        _seed_symbols(store)
        result = await generator.get_component_guide("test-repo", "Button")
        assert result["symbol_name"] == "Button"
        assert result["type"] == "struct"
        assert result["file"] == "lib/components/Button.ets"
        assert result.get("usage_example") is None

    async def test_generates_guide_with_llm(self, generator, store):
        _seed_symbols(store)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "description": "A reusable button component",
            "usage_example": 'Button({ text: "Submit" })',
        })

        import litellm
        litellm.acompletion = AsyncMock(return_value=mock_response)

        result = await generator.get_component_guide(
            "test-repo", "Button", llm_client={"model": "test-model"}
        )
        assert result["description"] == "A reusable button component"
        assert "Submit" in result["usage_example"]

        cached = generator.cache.get("test-repo", "component_guide", "Button")
        assert cached is not None
        assert cached["symbol_name"] == "Button"


class TestGetUsageExamples:
    async def test_finds_callers(self, generator, store):
        _seed_symbols(store)
        result = await generator.get_usage_examples("test-repo", "Button")
        assert len(result["examples"]) >= 1
        assert result["examples"][0]["file"] == "pages/HomePage.ets"

    async def test_excludes_definition_file(self, generator, store):
        _seed_symbols(store)
        result = await generator.get_usage_examples("test-repo", "Button")
        for ex in result["examples"]:
            assert ex["file"] != "lib/components/Button.ets"


class TestRecommendComponent:
    async def test_returns_cached(self, generator, cache):
        cached = {"requirement": "a button", "recommendations": []}
        cache.set("test-repo", "recommend", "a button", cached)

        result = await generator.recommend_component("test-repo", "a button")
        assert result["requirement"] == "a button"

    async def test_requires_llm(self, generator, store):
        _seed_symbols(store)
        result = await generator.recommend_component("test-repo", "a button")
        assert "error" in result


class TestGetApiGuide:
    async def test_returns_cached(self, generator, cache):
        cached = {"library_name": "biz_ui", "guide": {"description": "cached"}}
        cache.set("test-repo", "api_guide", "biz_ui", cached)

        result = await generator.get_api_guide("test-repo", "biz_ui")
        assert result["library_name"] == "biz_ui"
