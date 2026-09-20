"""Bounded, AST-based repository inspection with no code execution."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import Iterable, List, Sequence

from .models import FileInfo, PythonModuleInfo, RepositorySnapshot, utc_now


class RepositoryInspector:
    """Collect a bounded structural snapshot of an approved workspace."""

    SKIP_DIRS = {
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "uploads",
    }
    DEPLOYMENT_NAMES = {
        "Dockerfile",
        "docker-compose.yml",
        "render.yaml",
        "fly.toml",
        "railway.json",
        "DEPLOYMENT.md",
    }
    CONFIG_NAMES = {
        ".env.example",
        ".gitignore",
        ".dockerignore",
        "requirements.txt",
        "requirements-cloud.txt",
        "pyproject.toml",
        "setup.py",
        "package.json",
    }

    def __init__(
        self,
        root: Path,
        *,
        max_entries: int = 600,
        max_python_modules: int = 180,
        max_source_bytes: int = 512_000,
        git_timeout: float = 5.0,
    ) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise NotADirectoryError(str(root))
        self.max_entries = max_entries
        self.max_python_modules = max_python_modules
        self.max_source_bytes = max_source_bytes
        self.git_timeout = git_timeout

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _iter_files(self) -> Iterable[Path]:
        count = 0
        for path in sorted(self.root.rglob("*")):
            if any(part in self.SKIP_DIRS for part in path.parts):
                continue
            if not path.is_file():
                continue
            yield path
            count += 1
            if count >= self.max_entries:
                return

    @staticmethod
    def _kind(path: Path) -> str:
        if path.suffix == ".py":
            return "python"
        if path.name in RepositoryInspector.DEPLOYMENT_NAMES:
            return "deployment"
        if path.name in RepositoryInspector.CONFIG_NAMES or path.suffix in {".toml", ".yaml", ".yml", ".ini"}:
            return "config"
        if path.suffix in {".md", ".txt"}:
            return "documentation"
        if path.suffix in {".html", ".css", ".js"}:
            return "frontend"
        return "asset"

    def _git(self, *args: str) -> List[str]:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=self.root,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.git_timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if completed.returncode != 0:
            return []
        return [line for line in completed.stdout.splitlines() if line.strip()]

    def _inspect_python(self, path: Path) -> PythonModuleInfo:
        relative = self._relative(path)
        try:
            if path.stat().st_size > self.max_source_bytes:
                return PythonModuleInfo(relative, parse_error="source exceeds inspection limit")
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative)
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            return PythonModuleInfo(relative, parse_error=f"{type(exc).__name__}: {exc}")

        imports: set[str] = set()
        classes: set[str] = set()
        functions: set[str] = set()
        routes: set[str] = set()
        actions: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
            elif isinstance(node, ast.ClassDef):
                classes.add(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.add(node.name)
                for decorator in node.decorator_list:
                    text = ast.unparse(decorator) if hasattr(ast, "unparse") else ""
                    if any(marker in text for marker in ("app.", "router.", "route")):
                        routes.add(text[:160])
            elif isinstance(node, ast.Call):
                text = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
                if text.endswith("register") or text.endswith("register_action"):
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        actions.add(node.args[0].value)

        return PythonModuleInfo(
            path=relative,
            imports=sorted(item for item in imports if item),
            classes=sorted(classes),
            functions=sorted(functions),
            routes=sorted(routes),
            actions=sorted(actions),
        )

    def inspect(self) -> RepositorySnapshot:
        files: List[FileInfo] = []
        modules: List[PythonModuleInfo] = []
        tree: List[str] = []
        tests: List[str] = []
        configs: List[str] = []
        deployment_files: List[str] = []
        truncated = False

        for path in self._iter_files():
            relative = self._relative(path)
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            kind = self._kind(path)
            files.append(FileInfo(relative, kind, size))
            tree.append(relative)
            if path.name.startswith("test_") or "/tests/" in f"/{relative}" or relative.startswith("tests/"):
                tests.append(relative)
            if kind == "config":
                configs.append(relative)
            if kind == "deployment":
                deployment_files.append(relative)
            if path.suffix == ".py" and len(modules) < self.max_python_modules:
                modules.append(self._inspect_python(path))
            elif path.suffix == ".py":
                truncated = True

        if len(files) >= self.max_entries:
            truncated = True
        return RepositorySnapshot(
            root=str(self.root),
            generated_at=utc_now(),
            tree=tree,
            files=files,
            python_modules=modules,
            tests=sorted(tests),
            configs=sorted(configs),
            deployment_files=sorted(deployment_files),
            git_status=self._git("status", "--short"),
            git_diff_summary="\n".join(self._git("diff", "--stat")),
            recent_commits=self._git("log", "-5", "--oneline"),
            truncated=truncated,
        )
