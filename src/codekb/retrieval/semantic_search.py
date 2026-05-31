"""Semantic vector search for code and docs."""

from __future__ import annotations

from typing import Optional

from codekb.indexers.embedder import EmbeddingProvider, SentenceTransformerProvider
from codekb.storage.vector_store import VectorStore, SearchResult


class SemanticSearch:
    """Embed query → search ChromaDB for similar code/doc chunks."""

    def __init__(self, vector_store: VectorStore, provider: EmbeddingProvider):
        self.vector_store = vector_store
        self.provider = provider

    async def search_code(
        self,
        query: str,
        repo_name: Optional[str] = None,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """Semantic search across code chunks."""
        query_embedding = await self.provider.embed_query(query)
        return self.vector_store.search_code(query_embedding, repo_name=repo_name, top_k=top_k)

    async def search_docs(
        self,
        query: str,
        repo_name: Optional[str] = None,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """Semantic search across doc chunks."""
        query_embedding = await self.provider.embed_query(query)
        return self.vector_store.search_docs(query_embedding, repo_name=repo_name, top_k=top_k)

    async def search_all(
        self,
        query: str,
        repo_name: Optional[str] = None,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """Search across both code and doc chunks."""
        query_embedding = await self.provider.embed_query(query)
        code_results = self.vector_store.search_code(query_embedding, repo_name=repo_name, top_k=top_k)
        doc_results = self.vector_store.search_docs(query_embedding, repo_name=repo_name, top_k=top_k)
        all_results = code_results + doc_results
        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results[:top_k]
