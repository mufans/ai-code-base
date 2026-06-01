"""Index orchestrator: coordinates tree-sitter + embedding pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from codekb.core.config import CodekbYamlConfig, Settings, ensure_data_dir, load_settings
from codekb.core.module_detector import ModuleInfo, detect_modules
from codekb.core.repo_manager import RepoManager
from codekb.indexers.embedder import EmbeddingIndexer, create_embedding_provider
from codekb.indexers.tree_sitter import TreeSitterIndexer
from codekb.storage.doc_store import DocStore
from codekb.storage.sqlite_store import SqliteStore
from codekb.storage.vector_store import VectorStore


class IndexOrchestrator:
    """Coordinates full and incremental indexing pipeline."""

    def __init__(
        self,
        config: CodekbYamlConfig,
        store: SqliteStore,
        vector_store: VectorStore,
        doc_store: DocStore,
        repo_manager: RepoManager,
    ):
        self.config = config
        self.store = store
        self.vector_store = vector_store
        self.doc_store = doc_store
        self.repo_manager = repo_manager
        self.ts_indexer = TreeSitterIndexer(store)
        self.data_dir = ensure_data_dir(config)
        self.settings = load_settings()

    def _get_embedding_indexer(self, purpose: str = "code_embedding") -> EmbeddingIndexer:
        provider = create_embedding_provider(self.config, self.settings, purpose)
        return EmbeddingIndexer(self.store, self.vector_store, provider)

    async def full_index(self, repo_name: str) -> dict:
        """Run full index pipeline for a repo.

        Steps:
        1. Module detection
        2. Tree-sitter structure parsing
        3. Code embedding
        4. README/doc embedding
        """
        repo = self.store.get_repo(repo_name)
        if repo is None:
            raise ValueError(f"Repo not found: {repo_name}")

        repo_path = Path(repo.local_path)
        if not repo_path.exists():
            raise FileNotFoundError(f"Repo path not found: {repo_path}")

        # Detect modules
        configured_dicts = None
        module_configs = self.config.repo_modules.get(repo_name)
        if module_configs:
            configured_dicts = [m.model_dump() for m in module_configs]
        modules = detect_modules(repo_path, configured_dicts)

        # Save module detection results
        modules_data = [
            {"name": m.name, "path": m.path, "language": m.language, "source": m.source}
            for m in modules if m.name
        ]
        self.store.save_repo_modules(repo_name, modules_data)

        # Update status
        self.store.update_repo(repo_name, status="indexing")
        self.store.set_index_status(repo_name, "tree_sitter", "running")

        try:
            # Step 1: Tree-sitter parsing
            stats = self.ts_indexer.index_repo(
                repo_name, repo_path, self.config.index.exclude_patterns,
                modules=modules if any(m.name for m in modules) else None,
            )
            self.store.set_index_status(
                repo_name, "tree_sitter", "completed",
                items_processed=stats["files_indexed"],
            )

            # Step 1b: Doc index (lightweight, no vectorization)
            self.store.index_docs(repo_name, repo_path)

            # Step 2: Code embedding
            self.store.set_index_status(repo_name, "embedding_code", "running")
            emb_indexer = self._get_embedding_indexer("code_embedding")

            # Delete old code vectors
            await emb_indexer.delete_repo_vectors(repo_name)
            code_count = await emb_indexer.index_repo_code(repo_name)
            self.store.set_index_status(
                repo_name, "embedding_code", "completed",
                items_processed=code_count,
            )

            # Step 3: Doc embedding
            self.store.set_index_status(repo_name, "embedding_docs", "running")
            doc_indexer = self._get_embedding_indexer("doc_embedding")
            doc_count = await doc_indexer.index_repo_docs(repo_name, repo_path)
            self.store.set_index_status(
                repo_name, "embedding_docs", "completed",
                items_processed=doc_count,
            )

            # Update repo metadata
            self.store.update_repo(
                repo_name,
                status="indexed",
                file_count=stats["files_indexed"],
            )

            return {
                "repo": repo_name,
                "files_indexed": stats["files_indexed"],
                "symbols_found": stats["symbols_found"],
                "code_chunks_embedded": code_count,
                "doc_chunks_embedded": doc_count,
                "modules": [m.name for m in modules if m.name],
            }

        except Exception as e:
            self.store.update_repo(repo_name, status="error")
            self.store.set_index_status(repo_name, "full_index", "error", error_message=str(e))
            raise

    async def incremental_index(self, repo_name: str, changed_files: list[str]) -> dict:
        """Run incremental index for changed files.

        Steps:
        1. Re-parse changed source files with tree-sitter
        2. Re-embed changed file code chunks
        """
        repo = self.store.get_repo(repo_name)
        if repo is None:
            raise ValueError(f"Repo not found: {repo_name}")

        repo_path = Path(repo.local_path)

        # Load modules for file_to_module mapping
        modules_data = self.store.get_repo_modules(repo_name)
        modules = [ModuleInfo(name=m["name"], path=m.get("path", "")) for m in modules_data]

        source_extensions = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".rb", ".php"}
        doc_extensions = {".md", ".rst", ".txt"}

        source_files = [f for f in changed_files if Path(f).suffix in source_extensions]
        doc_files = [f for f in changed_files if Path(f).suffix in doc_extensions]

        stats = {"source_files_reindexed": 0, "doc_files_reindexed": 0}

        # Re-index source files
        if source_files:
            emb_indexer = self._get_embedding_indexer("code_embedding")
            for rel_path in source_files:
                file_path = repo_path / rel_path
                if not file_path.exists():
                    # File was deleted - remove its data
                    self.store.delete_symbols_for_file(repo_name, rel_path)
                    self.store.delete_calls_for_file(repo_name, rel_path)
                    self.store.delete_imports_for_file(repo_name, rel_path)
                    await emb_indexer.delete_file_vectors(repo_name, rel_path)
                    continue

                # Determine module for this file
                mod_name = ""
                if modules:
                    from codekb.core.module_detector import file_to_module
                    mod_name = file_to_module(rel_path, modules)

                # Re-parse
                self.ts_indexer.reindex_file(repo_name, file_path, rel_path, repo_module=mod_name)

                # Re-embed
                symbols = self.store.get_symbols(repo_name, rel_path)
                if symbols:
                    from codekb.indexers.embedder import chunk_symbols
                    chunks = chunk_symbols(symbols)
                    await emb_indexer.delete_file_vectors(repo_name, rel_path)
                    if chunks:
                        texts = [c.source for c in chunks]
                        embeddings = await emb_indexer.provider.embed(texts)
                        self.vector_store.add_code_chunks(chunks, embeddings)

                stats["source_files_reindexed"] += 1

        # Re-embed doc files
        if doc_files:
            doc_indexer = self._get_embedding_indexer("doc_embedding")
            for rel_path in doc_files:
                file_path = repo_path / rel_path
                if file_path.exists():
                    content = file_path.read_text(encoding="utf-8", errors="replace")

                    # Determine module for this file
                    mod_name = ""
                    if modules:
                        from codekb.core.module_detector import file_to_module
                        mod_name = file_to_module(rel_path, modules)

                    from codekb.indexers.embedder import chunk_markdown
                    chunks = chunk_markdown(content, repo_name, rel_path, repo_module=mod_name)
                    await doc_indexer.delete_file_vectors(repo_name, rel_path)
                    if chunks:
                        texts = [c.content for c in chunks]
                        embeddings = await doc_indexer.provider.embed(texts)
                        self.vector_store.add_doc_chunks(chunks, embeddings)
                    stats["doc_files_reindexed"] += 1

        return stats
