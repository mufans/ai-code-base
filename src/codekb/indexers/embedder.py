"""Embedding pipeline: chunk code by symbol boundaries and embed."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional, Protocol

from codekb.core.config import CodekbYamlConfig, Settings
from codekb.storage.sqlite_store import SqliteStore, Symbol
from codekb.storage.vector_store import VectorStore, CodeChunk, DocChunk


class EmbeddingProvider(Protocol):
    """Protocol for embedding providers."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    async def embed_query(self, text: str) -> list[float]:
        ...


class SentenceTransformerProvider:
    """Local sentence-transformers embedding provider."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        embeddings = model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()

    async def embed_query(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result[0]


class OpenAIEmbeddingProvider:
    """OpenAI API embedding provider."""

    def __init__(self, model: str = "text-embedding-3-small",
                 base_url: Optional[str] = None, api_key: Optional[str] = None):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import openai
        client = openai.AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )
        response = await client.embeddings.create(
            model=self.model,
            input=texts,
        )
        return [item.embedding for item in response.data]

    async def embed_query(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result[0]


def _chunk_id(repo_name: str, file_path: str, name: str, kind: str, start_line: int = 0) -> str:
    """Generate a deterministic chunk ID."""
    raw = f"{repo_name}:{file_path}:{name}:{kind}:{start_line}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _doc_chunk_id(repo_name: str, file_path: str, section: str, index: int) -> str:
    """Generate a deterministic doc chunk ID."""
    raw = f"{repo_name}:{file_path}:{section}:{index}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def chunk_symbols(symbols: list[Symbol]) -> list[CodeChunk]:
    """Convert symbols to code chunks for embedding."""
    chunks = []
    for sym in symbols:
        if not sym.source:
            continue
        chunk = CodeChunk(
            id=_chunk_id(sym.repo_name, sym.file_path, sym.name, sym.kind, sym.start_line),
            repo_name=sym.repo_name,
            file_path=sym.file_path,
            name=sym.name,
            kind=sym.kind,
            source=sym.source,
            start_line=sym.start_line,
            end_line=sym.end_line,
            language=sym.language,
        )
        chunks.append(chunk)
    return chunks


def chunk_markdown(content: str, repo_name: str, file_path: str) -> list[DocChunk]:
    """Chunk markdown content by section headings."""
    # Split by headings
    sections = re.split(r'(?=^#{1,3}\s)', content, flags=re.MULTILINE)
    chunks = []
    current_title = file_path

    for i, section in enumerate(sections):
        section = section.strip()
        if not section:
            continue

        # Extract title from first heading
        title_match = re.match(r'^#{1,3}\s+(.+)', section)
        if title_match:
            current_title = title_match.group(1).strip()

        chunk = DocChunk(
            id=_doc_chunk_id(repo_name, file_path, current_title, i),
            repo_name=repo_name,
            file_path=file_path,
            title=current_title,
            content=section,
            section=current_title,
        )
        chunks.append(chunk)

    return chunks


class EmbeddingIndexer:
    """Reads symbols from structure.db, chunks, embeds, stores in ChromaDB."""

    def __init__(
        self,
        store: SqliteStore,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        batch_size: int = 32,
    ):
        self.store = store
        self.vector_store = vector_store
        self.provider = embedding_provider
        self.batch_size = batch_size

    async def index_repo_code(self, repo_name: str):
        """Embed all code symbols for a repo."""
        symbols = self.store.get_symbols(repo_name)
        if not symbols:
            return 0

        chunks = chunk_symbols(symbols)
        return await self._embed_and_store_code(chunks)

    async def index_repo_docs(self, repo_name: str, repo_path: Path):
        """Embed README and docs for a repo."""
        count = 0

        # Embed README
        readme_path = self._find_readme(repo_path)
        if readme_path:
            content = readme_path.read_text(encoding="utf-8", errors="replace")
            chunks = chunk_markdown(content, repo_name, f"README.md")
            count += await self._embed_and_store_docs(chunks)

        # Embed docs/ directory markdown files
        docs_dir = repo_path / "docs"
        if docs_dir.exists():
            for md_file in docs_dir.rglob("*.md"):
                rel_path = str(md_file.relative_to(repo_path))
                content = md_file.read_text(encoding="utf-8", errors="replace")
                chunks = chunk_markdown(content, repo_name, rel_path)
                count += await self._embed_and_store_docs(chunks)

        return count

    async def index_generated_docs(self, repo_name: str, generated_dir: Path):
        """Embed generated architecture docs."""
        count = 0
        repo_gen_dir = generated_dir / repo_name
        if not repo_gen_dir.exists():
            return 0

        for md_file in repo_gen_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8", errors="replace")
            if not content.strip():
                continue
            chunks = chunk_markdown(content, repo_name, str(md_file.relative_to(generated_dir)))
            count += await self._embed_and_store_docs(chunks)

        return count

    async def _embed_and_store_code(self, chunks: list[CodeChunk]) -> int:
        """Embed code chunks in batches and store in ChromaDB."""
        if not chunks:
            return 0

        total = 0
        for i in range(0, len(chunks), self.batch_size):
            batch = chunks[i:i + self.batch_size]
            texts = [c.source for c in batch]
            embeddings = await self.provider.embed(texts)
            self.vector_store.add_code_chunks(batch, embeddings)
            total += len(batch)

        return total

    async def _embed_and_store_docs(self, chunks: list[DocChunk]) -> int:
        """Embed doc chunks in batches and store in ChromaDB."""
        if not chunks:
            return 0

        total = 0
        for i in range(0, len(chunks), self.batch_size):
            batch = chunks[i:i + self.batch_size]
            texts = [c.content for c in batch]
            embeddings = await self.provider.embed(texts)
            self.vector_store.add_doc_chunks(batch, embeddings)
            total += len(batch)

        return total

    def _find_readme(self, repo_path: Path) -> Optional[Path]:
        """Find README file in repo root."""
        for name in ["README.md", "README.rst", "README.txt", "README"]:
            path = repo_path / name
            if path.exists():
                return path
        return None

    async def delete_repo_vectors(self, repo_name: str):
        """Delete all vectors for a repo."""
        self.vector_store.delete_repo_chunks(repo_name)

    async def delete_file_vectors(self, repo_name: str, file_path: str):
        """Delete vectors for a specific file."""
        self.vector_store.delete_file_chunks(repo_name, file_path)


def create_embedding_provider(config: CodekbYamlConfig, settings: Settings,
                               purpose: str = "code_embedding") -> EmbeddingProvider:
    """Create an embedding provider based on config assignments."""
    assignment = getattr(config.assignments, purpose, "local")
    provider_config = config.embedding_providers.get(assignment)

    if provider_config is None:
        # Fallback to local
        return SentenceTransformerProvider()

    if provider_config.provider == "sentence-transformers":
        return SentenceTransformerProvider(model_name=provider_config.model)
    elif provider_config.provider == "openai":
        api_key = settings.OPENAI_API_KEY
        if provider_config.model and "codestral" in provider_config.model:
            api_key = settings.MISTRAL_API_KEY or settings.OPENAI_API_KEY
        return OpenAIEmbeddingProvider(
            model=provider_config.model,
            base_url=provider_config.base_url,
            api_key=api_key,
        )
    else:
        return SentenceTransformerProvider()
