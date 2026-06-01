"""Tests for module detector."""

from pathlib import Path

import pytest

from codekb.core.module_detector import (
    ModuleInfo,
    detect_modules,
    file_to_module,
)


class TestDetectModules:
    def test_single_module_repo(self, tmp_path):
        """A flat repo with no monorepo structure returns single empty module."""
        (tmp_path / "main.py").write_text("def main(): pass")
        (tmp_path / "utils.py").write_text("def util(): pass")

        modules = detect_modules(tmp_path)
        assert len(modules) == 1
        assert modules[0].name == ""
        assert modules[0].path == ""

    def test_monorepo_packages(self, tmp_path):
        """Detect packages/ directory with manifest files."""
        core = tmp_path / "packages" / "core"
        core.mkdir(parents=True)
        (core / "package.json").write_text('{"name": "core"}')
        (core / "index.js").write_text("module.exports = {};")

        api = tmp_path / "packages" / "api"
        api.mkdir(parents=True)
        (api / "package.json").write_text('{"name": "api"}')
        (api / "index.js").write_text("module.exports = {};")

        modules = detect_modules(tmp_path)
        assert len(modules) == 2
        names = {m.name for m in modules}
        assert "core" in names
        assert "api" in names
        for m in modules:
            assert m.source == "auto"

    def test_monorepo_python_packages(self, tmp_path):
        """Detect Python packages in packages/ directory."""
        backend = tmp_path / "packages" / "backend"
        backend.mkdir(parents=True)
        (backend / "pyproject.toml").write_text("[project]\nname = 'backend'")

        frontend = tmp_path / "packages" / "frontend"
        frontend.mkdir(parents=True)
        (frontend / "package.json").write_text('{"name": "frontend"}')

        modules = detect_modules(tmp_path)
        assert len(modules) == 2
        names = {m.name for m in modules}
        assert "backend" in names
        assert "frontend" in names

    def test_root_level_manifest_dirs(self, tmp_path):
        """Detect modules at root level with manifest files."""
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text('{"name": "frontend"}')

        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "pyproject.toml").write_text("[project]\nname = 'backend'")

        modules = detect_modules(tmp_path)
        assert len(modules) == 2
        names = {m.name for m in modules}
        assert "frontend" in names
        assert "backend" in names

    def test_common_module_dirs_no_manifest(self, tmp_path):
        """Detect frontend/backend dirs even without manifest files."""
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "app.js").write_text("")

        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "app.py").write_text("")

        modules = detect_modules(tmp_path)
        assert len(modules) == 2
        names = {m.name for m in modules}
        assert "frontend" in names
        assert "backend" in names

    def test_manual_config_overrides(self, tmp_path):
        """Manual config takes priority over auto-detection."""
        # Create auto-detectable structure
        core = tmp_path / "packages" / "core"
        core.mkdir(parents=True)
        (core / "package.json").write_text('{"name": "core"}')

        # But provide manual config with different names
        configured = [
            {"name": "module-a", "path": "src/a"},
            {"name": "module-b", "path": "src/b"},
        ]
        modules = detect_modules(tmp_path, configured_modules=configured)
        assert len(modules) == 2
        names = {m.name for m in modules}
        assert "module-a" in names
        assert "module-b" in names
        for m in modules:
            assert m.source == "manual"

    def test_empty_monorepo_dir(self, tmp_path):
        """packages/ exists but subdirs have no manifest -> single module."""
        packages = tmp_path / "packages"
        packages.mkdir()
        core = packages / "core"
        core.mkdir()
        # No manifest file

        modules = detect_modules(tmp_path)
        assert len(modules) == 1
        assert modules[0].name == ""

    def test_single_package_returns_single_module(self, tmp_path):
        """Only one package detected -> single module for backward compat."""
        core = tmp_path / "packages" / "core"
        core.mkdir(parents=True)
        (core / "package.json").write_text('{"name": "core"}')

        modules = detect_modules(tmp_path)
        # Only 1 module -> backward compat single-module
        assert len(modules) == 1
        assert modules[0].name == ""

    def test_services_dir(self, tmp_path):
        """Detect services/ directory structure."""
        auth = tmp_path / "services" / "auth"
        auth.mkdir(parents=True)
        (auth / "package.json").write_text('{"name": "auth"}')

        user = tmp_path / "services" / "user"
        user.mkdir(parents=True)
        (user / "package.json").write_text('{"name": "user"}')

        modules = detect_modules(tmp_path)
        assert len(modules) == 2
        names = {m.name for m in modules}
        assert "auth" in names
        assert "user" in names


class TestFileToModule:
    def test_file_in_module(self):
        modules = [
            ModuleInfo(name="frontend", path="frontend"),
            ModuleInfo(name="backend", path="backend"),
        ]
        assert file_to_module("frontend/src/app.js", modules) == "frontend"
        assert file_to_module("backend/api/routes.py", modules) == "backend"

    def test_file_not_in_any_module(self):
        modules = [
            ModuleInfo(name="frontend", path="frontend"),
        ]
        assert file_to_module("README.md", modules) == ""

    def test_longest_prefix_match(self):
        modules = [
            ModuleInfo(name="app", path="packages/app"),
            ModuleInfo(name="packages", path="packages"),
        ]
        # Should match the longest prefix
        result = file_to_module("packages/app/main.ts", modules)
        assert result == "app"

    def test_empty_modules(self):
        assert file_to_module("any/file.py", []) == ""

    def test_single_module_repo(self):
        modules = [ModuleInfo(name="", path="")]
        assert file_to_module("src/main.py", modules) == ""

    def test_nested_packages(self):
        modules = [
            ModuleInfo(name="core", path="packages/core"),
            ModuleInfo(name="api", path="packages/api"),
        ]
        assert file_to_module("packages/core/src/index.ts", modules) == "core"
        assert file_to_module("packages/api/routes.ts", modules) == "api"
        assert file_to_module("README.md", modules) == ""


class TestModuleInfoLanguageDetection:
    def test_javascript_from_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        from codekb.core.module_detector import _find_manifest, _guess_language
        manifest = _find_manifest(tmp_path)
        assert manifest is not None
        assert _guess_language(manifest) == "javascript"

    def test_python_from_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]")
        from codekb.core.module_detector import _find_manifest, _guess_language
        manifest = _find_manifest(tmp_path)
        assert manifest is not None
        assert _guess_language(manifest) == "python"

    def test_rust_from_cargo(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]")
        from codekb.core.module_detector import _find_manifest, _guess_language
        manifest = _find_manifest(tmp_path)
        assert manifest is not None
        assert _guess_language(manifest) == "rust"
