"""Tests for guide cache wrapper."""

import json

import pytest

from codekb.mcp.guide_cache import GuideCache
from codekb.storage.sqlite_store import SqliteStore


@pytest.fixture
def cache(tmp_path):
    store = SqliteStore(tmp_path / "index")
    return GuideCache(store)


class TestGuideCache:
    def test_get_returns_none_on_miss(self, cache):
        result = cache.get("repo", "component_guide", "Foo")
        assert result is None

    def test_set_and_get(self, cache):
        data = {"symbol_name": "Foo", "description": "A foo component"}
        cache.set("repo", "component_guide", "Foo", data)
        result = cache.get("repo", "component_guide", "Foo")
        assert result == data

    def test_get_json_returns_raw_string(self, cache):
        cache.set("repo", "component_guide", "Foo", {"v": 1})
        raw = cache.get_json("repo", "component_guide", "Foo")
        assert raw is not None
        assert json.loads(raw) == {"v": 1}

    def test_set_overwrites(self, cache):
        cache.set("repo", "component_guide", "Foo", {"v": 1})
        cache.set("repo", "component_guide", "Foo", {"v": 2})
        result = cache.get("repo", "component_guide", "Foo")
        assert result == {"v": 2}

    def test_clear_by_repo(self, cache):
        cache.set("repo-a", "component_guide", "Foo", {"a": 1})
        cache.set("repo-b", "component_guide", "Bar", {"b": 2})
        cache.clear("repo-a")
        assert cache.get("repo-a", "component_guide", "Foo") is None
        assert cache.get("repo-b", "component_guide", "Bar") == {"b": 2}
