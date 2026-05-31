"""ChromaDB vector store for semantic code/doc search."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import chromadb
from pydantic import BaseModel


class CodeChunk(BaseModel):
    """A code chunk stored in ChromaDB."""
    id: str
    repo_name: str
    file_path: str
    name: str
    kind: str
    source: str
    start_line: int
    end_line: int
    language: str
    chunk_type: str = "code"  # code, doc, readme


class DocChunk(BaseModel):
    """A documentation chunk stored in ChromaDB."""
    id: str
    repo_name: str
    file_path: str
    title: str
    content: str
    section: str = ""
    chunk_type: str = "doc"


class SearchResult(BaseModel):
    """A search result from vector or hybrid search."""
    id: str
    repo_name: str
    file_path: str
    content: str
    score: float
    metadata: dict = {}


class VectorStore:
    """Manages ChromaDB collections for code and doc vectors."""

    def __init__(self, vectors_dir: Path):
        self.vectors_dir = vectors_dir
        self.vectors_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(vectors_dir))
        self._code_collection = self._client.get_or_create_collection(
            name="code_chunks",
            metadata={"hnsw:space": "cosine"},
        )
        self._doc_collection = self._client.get_or_create_collection(
            name="doc_chunks",
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def code_collection(self):
        return self._code_collection

    @property
    def doc_collection(self):
        return self._doc_collection

    def add_code_chunks(self, chunks: list[CodeChunk], embeddings: list[list[float]]):
        """Add code chunks with pre-computed embeddings."""
        if not chunks:
            return
        self._code_collection.upsert(
            ids=[c.id for c in chunks],
            embeddings=embeddings,
            documents=[c.source for c in chunks],
            metadatas=[{
                "repo_name": c.repo_name,
                "file_path": c.file_path,
                "name": c.name,
                "kind": c.kind,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "language": c.language,
                "chunk_type": c.chunk_type,
            } for c in chunks],
        )

    def add_doc_chunks(self, chunks: list[DocChunk], embeddings: list[list[float]]):
        """Add doc chunks with pre-computed embeddings."""
        if not chunks:
            return
        self._doc_collection.upsert(
            ids=[c.id for c in chunks],
            embeddings=embeddings,
            documents=[c.content for c in chunks],
            metadatas=[{
                "repo_name": c.repo_name,
                "file_path": c.file_path,
                "title": c.title,
                "section": c.section,
                "chunk_type": c.chunk_type,
            } for c in chunks],
        )

    def search_code(self, query_embedding: list[float], repo_name: Optional[str] = None,
                    top_k: int = 10) -> list[SearchResult]:
        """Search code chunks by embedding."""
        where_filter = None
        if repo_name:
            where_filter = {"repo_name": repo_name}

        results = self._code_collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, self._code_collection.count() if self._code_collection.count() > 0 else 1),
            where=where_filter,
        )

        search_results = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i] if results["distances"] else 0
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                document = results["documents"][0][i] if results["documents"] else ""
                search_results.append(SearchResult(
                    id=doc_id,
                    repo_name=metadata.get("repo_name", ""),
                    file_path=metadata.get("file_path", ""),
                    content=document,
                    score=1 - distance,  # cosine distance → similarity
                    metadata=metadata,
                ))
        return search_results

    def search_docs(self, query_embedding: list[float], repo_name: Optional[str] = None,
                    top_k: int = 10) -> list[SearchResult]:
        """Search doc chunks by embedding."""
        where_filter = None
        if repo_name:
            where_filter = {"repo_name": repo_name}

        count = self._doc_collection.count()
        if count == 0:
            return []

        results = self._doc_collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, count),
            where=where_filter,
        )

        search_results = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i] if results["distances"] else 0
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                document = results["documents"][0][i] if results["documents"] else ""
                search_results.append(SearchResult(
                    id=doc_id,
                    repo_name=metadata.get("repo_name", ""),
                    file_path=metadata.get("file_path", ""),
                    content=document,
                    score=1 - distance,
                    metadata=metadata,
                ))
        return search_results

    def delete_repo_chunks(self, repo_name: str):
        """Delete all chunks for a repo from both collections."""
        for collection in [self._code_collection, self._doc_collection]:
            try:
                collection.delete(where={"repo_name": repo_name})
            except Exception:
                pass

    def delete_file_chunks(self, repo_name: str, file_path: str):
        """Delete chunks for a specific file."""
        for collection in [self._code_collection, self._doc_collection]:
            try:
                collection.delete(
                    where={"$and": [{"repo_name": repo_name}, {"file_path": file_path}]}
                )
            except Exception:
                pass
