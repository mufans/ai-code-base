"""Hybrid search combining vector semantic search with keyword matching."""

from __future__ import annotations

import re
from typing import Optional

from codekb.retrieval.semantic_search import SemanticSearch
from codekb.retrieval.structure_query import StructureQuery
from codekb.storage.sqlite_store import SqliteStore
from codekb.storage.vector_store import SearchResult


def _keyword_search(query: str, items: list[SearchResult], top_k: int = 20) -> list[tuple[str, float]]:
    """Simple keyword matching with BM25-like scoring."""
    query_terms = set(re.findall(r'\w+', query.lower()))
    scores: dict[str, float] = {}

    for item in items:
        text = (item.content + " " + item.metadata.get("name", "")).lower()
        text_terms = set(re.findall(r'\w+', text))
        matching = query_terms.intersection(text_terms)

        if matching:
            # Simple scoring: ratio of matching terms
            score = len(matching) / len(query_terms) if query_terms else 0
            scores[item.id] = score

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return ranked[:top_k]


def reciprocal_rank_fusion(
    *rankings: list[tuple[str, float]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion for combining multiple rankings.

    RRF score = sum(1 / (k + rank)) for each ranking
    """
    fused: dict[str, float] = {}

    for ranking in rankings:
        for rank, (doc_id, _score) in enumerate(ranking):
            fused[doc_id] = fused.get(doc_id, 0) + 1.0 / (k + rank + 1)

    return sorted(fused.items(), key=lambda x: x[1], reverse=True)


class HybridSearch:
    """Hybrid search combining vector + keyword with Reciprocal Rank Fusion."""

    def __init__(self, semantic_search: SemanticSearch, store: SqliteStore):
        self.semantic = semantic_search
        self.store = store

    async def search(
        self,
        query: str,
        repo_name: Optional[str] = None,
        repo_module: Optional[str] = None,
        top_k: int = 10,
        code_weight: float = 0.7,
        doc_weight: float = 0.3,
    ) -> list[SearchResult]:
        """Hybrid search: vector + keyword, fused with RRF."""
        # Vector search
        code_results = await self.semantic.search_code(query, repo_name=repo_name,
                                                        repo_module=repo_module, top_k=top_k * 3)
        doc_results = await self.semantic.search_docs(query, repo_name=repo_name,
                                                       repo_module=repo_module, top_k=top_k * 3)

        # Keyword search
        code_keyword = _keyword_search(query, code_results, top_k=top_k * 3)
        doc_keyword = _keyword_search(query, doc_results, top_k=top_k * 3)

        # Vector ranking
        code_vector_ranking = [(r.id, r.score) for r in code_results]
        doc_vector_ranking = [(r.id, r.score) for r in doc_results]

        # Fuse all rankings
        fused = reciprocal_rank_fusion(
            code_vector_ranking,
            doc_vector_ranking,
            code_keyword,
            doc_keyword,
        )

        # Build result map
        all_results = {r.id: r for r in code_results + doc_results}

        # Return top fused results
        results = []
        for doc_id, score in fused[:top_k]:
            if doc_id in all_results:
                result = all_results[doc_id]
                results.append(SearchResult(
                    id=result.id,
                    repo_name=result.repo_name,
                    file_path=result.file_path,
                    content=result.content,
                    score=score,
                    metadata=result.metadata,
                ))

        return results
