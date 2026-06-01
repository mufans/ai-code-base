"""Configuration loading from YAML + .env files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProviderConfig(BaseModel):
    """Configuration for an LLM provider."""

    provider: str
    base_url: Optional[str] = None
    api_version: Optional[str] = None
    model: Optional[str] = None


class EmbeddingProviderConfig(BaseModel):
    """Configuration for an embedding provider."""

    provider: str
    model: str = "all-MiniLM-L6-v2"
    dimension: int = 384
    base_url: Optional[str] = None


class AssignmentsConfig(BaseModel):
    """Assignment of providers to tasks."""

    doc_generation: str = "openai"
    code_embedding: str = "local"
    doc_embedding: str = "local"


class WebhookConfig(BaseModel):
    """Webhook receiver configuration."""

    port: int = 8080


class IndexConfig(BaseModel):
    """Index building configuration."""

    chunk_size: int = 512
    chunk_overlap: int = 64
    max_file_size: int = 1048576  # 1MB
    exclude_patterns: list[str] = Field(
        default_factory=lambda: [
            "*.min.js",
            "*.lock",
            "node_modules/**",
            "__pycache__/**",
            ".git/**",
            "dist/**",
            "build/**",
        ]
    )


class ModuleConfig(BaseModel):
    """Configuration for a module within a multi-module repository."""

    name: str
    path: str = ""
    language: str = ""


class CodekbYamlConfig(BaseModel):
    """Top-level codekb.yaml configuration."""

    data_dir: str = "~/.codekb"
    llm_providers: dict[str, LLMProviderConfig] = Field(default_factory=dict)
    embedding_providers: dict[str, EmbeddingProviderConfig] = Field(
        default_factory=lambda: {
            "local": EmbeddingProviderConfig(
                provider="sentence-transformers",
                model="all-MiniLM-L6-v2",
                dimension=384,
            )
        }
    )
    assignments: AssignmentsConfig = Field(default_factory=AssignmentsConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
    repo_modules: dict[str, list[ModuleConfig]] = Field(default_factory=dict)


class Settings(BaseSettings):
    """Environment-based settings loaded from .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    MISTRAL_API_KEY: Optional[str] = None
    AZURE_API_KEY: Optional[str] = None
    WEBHOOK_SECRET: Optional[str] = None


def load_config(config_path: Optional[str | Path] = None) -> CodekbYamlConfig:
    """Load configuration from a YAML file.

    Searches for codekb.yaml in the following order:
    1. Explicit config_path parameter
    2. CODEKB_CONFIG environment variable
    3. ./codekb.yaml (current directory)
    4. ~/.codekb/codekb.yaml
    """
    if config_path is not None:
        path = Path(config_path)
        if not path.exists():
            return CodekbYamlConfig()
    elif os.environ.get("CODEKB_CONFIG"):
        path = Path(os.environ["CODEKB_CONFIG"])
    elif Path("codekb.yaml").exists():
        path = Path("codekb.yaml")
    elif Path.home().joinpath(".codekb", "codekb.yaml").exists():
        path = Path.home().joinpath(".codekb", "codekb.yaml")
    else:
        return CodekbYamlConfig()

    with open(path) as f:
        raw = yaml.safe_load(f)

    if raw is None:
        return CodekbYamlConfig()

    return CodekbYamlConfig(**raw.get("codekb", {}))


def load_settings(env_file: Optional[str | Path] = None) -> Settings:
    """Load environment settings from .env file."""
    if env_file is not None:
        return Settings(_env_file=env_file)
    return Settings()


def get_data_dir(config: Optional[CodekbYamlConfig] = None) -> Path:
    """Get the resolved data directory path."""
    if config is None:
        config = CodekbYamlConfig()
    return Path(config.data_dir).expanduser().resolve()


def ensure_data_dir(config: Optional[CodekbYamlConfig] = None) -> Path:
    """Create data directory structure if it doesn't exist.

    Creates:
    - ~/.codekb/repos/
    - ~/.codekb/index/
    - ~/.codekb/index/vectors/
    - ~/.codekb/generated/
    """
    data_dir = get_data_dir(config)
    dirs = [
        data_dir / "repos",
        data_dir / "index",
        data_dir / "index" / "vectors",
        data_dir / "generated",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    return data_dir
