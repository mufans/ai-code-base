"""Tests for configuration loading."""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from codekb.core.config import (
    CodekbYamlConfig,
    Settings,
    ensure_data_dir,
    load_config,
    load_settings,
)


class TestCodekbYamlConfig:
    def test_defaults(self):
        config = CodekbYamlConfig()
        assert config.data_dir == "~/.codekb"
        assert config.webhook.port == 8080
        assert config.index.chunk_size == 512

    def test_custom_values(self):
        config = CodekbYamlConfig(
            data_dir="/tmp/codekb-test",
            webhook={"port": 9090},
            index={"chunk_size": 1024},
        )
        assert config.data_dir == "/tmp/codekb-test"
        assert config.webhook.port == 9090
        assert config.index.chunk_size == 1024

    def test_embedding_providers_default(self):
        config = CodekbYamlConfig()
        assert "local" in config.embedding_providers
        assert config.embedding_providers["local"].provider == "sentence-transformers"
        assert config.embedding_providers["local"].dimension == 384

    def test_assignments_default(self):
        config = CodekbYamlConfig()
        assert config.assignments.doc_generation == "openai"
        assert config.assignments.code_embedding == "local"


class TestLoadConfig:
    def test_load_from_file(self, tmp_path):
        config_data = {
            "codekb": {
                "data_dir": "/tmp/test-codekb",
                "webhook": {"port": 9999},
            }
        }
        config_file = tmp_path / "codekb.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(config_file)
        assert config.data_dir == "/tmp/test-codekb"
        assert config.webhook.port == 9999

    def test_load_missing_file_returns_default(self):
        config = load_config("/nonexistent/path/codekb.yaml")
        # Should return defaults since no file found at any location
        assert isinstance(config, CodekbYamlConfig)

    def test_load_empty_file(self, tmp_path):
        config_file = tmp_path / "codekb.yaml"
        config_file.write_text("")

        config = load_config(config_file)
        assert isinstance(config, CodekbYamlConfig)
        assert config.data_dir == "~/.codekb"

    def test_load_full_config(self, tmp_path):
        config_data = {
            "codekb": {
                "data_dir": "~/.codekb",
                "llm_providers": {
                    "openai": {"provider": "openai"},
                    "anthropic": {"provider": "anthropic"},
                },
                "embedding_providers": {
                    "local": {
                        "provider": "sentence-transformers",
                        "model": "all-MiniLM-L6-v2",
                        "dimension": 384,
                    },
                },
                "assignments": {
                    "doc_generation": "openai",
                    "code_embedding": "local",
                    "doc_embedding": "local",
                },
            }
        }
        config_file = tmp_path / "codekb.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(config_file)
        assert "openai" in config.llm_providers
        assert config.llm_providers["openai"].provider == "openai"
        assert "anthropic" in config.llm_providers


class TestSettings:
    def test_load_from_env_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_API_KEY=sk-test-123\nANTHROPIC_API_KEY=sk-ant-test\n")

        # Clear env vars to ensure only .env file is used
        env_vars = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]
        saved = {}
        for var in env_vars:
            saved[var] = os.environ.pop(var, None)

        try:
            settings = load_settings(env_file)
            assert settings.OPENAI_API_KEY == "sk-test-123"
            assert settings.ANTHROPIC_API_KEY == "sk-ant-test"
        finally:
            for var, val in saved.items():
                if val is not None:
                    os.environ[var] = val

    def test_defaults_none(self):
        # Clear any env vars that might interfere
        env_vars = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MISTRAL_API_KEY", "AZURE_API_KEY", "WEBHOOK_SECRET"]
        saved = {}
        for var in env_vars:
            saved[var] = os.environ.pop(var, None)

        try:
            settings = Settings(_env_file=None)
            assert settings.OPENAI_API_KEY is None
            assert settings.WEBHOOK_SECRET is None
        finally:
            for var, val in saved.items():
                if val is not None:
                    os.environ[var] = val


class TestEnsureDataDir:
    def test_creates_directory_structure(self, tmp_path):
        config = CodekbYamlConfig(data_dir=str(tmp_path / "test-codekb"))
        data_dir = ensure_data_dir(config)

        assert (data_dir / "repos").is_dir()
        assert (data_dir / "index").is_dir()
        assert (data_dir / "index" / "vectors").is_dir()
        assert (data_dir / "generated").is_dir()

    def test_idempotent(self, tmp_path):
        config = CodekbYamlConfig(data_dir=str(tmp_path / "test-codekb"))
        ensure_data_dir(config)
        data_dir = ensure_data_dir(config)
        assert data_dir.is_dir()
