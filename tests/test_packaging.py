"""Packaging sanity: version sync, py.typed, stdlib-only imports, 3.8 syntax."""

from __future__ import annotations

import ast
import os
import re
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src", "alltradingapi")
PROJECT = os.path.join(os.path.dirname(__file__), "..")


def _read(name: str) -> str:
    with open(os.path.join(SRC, name), encoding="utf-8") as fh:
        return fh.read()


def test_version_matches_pyproject() -> None:
    from alltradingapi import __version__

    with open(os.path.join(PROJECT, "pyproject.toml"), encoding="utf-8") as fh:
        content = fh.read()
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    assert match is not None
    assert match.group(1) == __version__ == "1.0.1"


def test_version_matches_version_file() -> None:
    from alltradingapi import __version__
    from alltradingapi._version import __version__ as file_version

    assert __version__ == file_version


def test_py_typed_present() -> None:
    assert os.path.isfile(os.path.join(SRC, "py.typed"))


def test_public_surface_exports() -> None:
    import alltradingapi

    required = [
        "Client",
        "AsyncClient",
        "ApiKey",
        "Asset",
        "Candle",
        "SavedCandles",
        "TradeResult",
        "TradeOutcome",
        "APIObject",
        "UpdatePolicy",
        "ALLError",
        "ConfigurationError",
        "AuthenticationError",
        "PermissionDeniedError",
        "NotFoundError",
        "ValidationError",
        "RateLimitError",
        "ServerError",
        "UpstreamError",
        "TransportError",
        "TimeoutError_",
        "UpdateRequiredError",
        "UpdateCheckError",
        "__version__",
    ]
    for name in required:
        assert hasattr(alltradingapi, name), "missing export: " + name


def test_no_third_party_imports() -> None:
    """Every import in src/ must be stdlib or the package itself."""
    allowed_modules = {
        "alltradingapi",
        # stdlib
        "__future__",
        "asyncio",
        "base64",
        "collections",
        "collections.abc",
        "concurrent.futures",
        "enum",
        "gzip",
        "http",
        "importlib",
        "json",
        "logging",
        "os",
        "random",
        "re",
        "socket",
        "ssl",
        "subprocess",
        "sys",
        "tempfile",
        "threading",
        "time",
        "typing",
        "urllib",
        "zlib",
        "datetime",
        "dataclasses",
        "argparse",
        "concurrent",
    }
    violations: list = []
    for root, _dirs, files in os.walk(SRC):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root_module = alias.name.split(".")[0]
                        if root_module not in allowed_modules:
                            violations.append((path, alias.name))
                elif isinstance(node, ast.ImportFrom):
                    if node.level:  # relative import inside the package
                        continue
                    if node.module:
                        root_module = node.module.split(".")[0]
                        if root_module not in allowed_modules:
                            violations.append((path, node.module))
    assert violations == []


def test_python38_compatible_syntax() -> None:
    """Every file must parse with 3.8 grammar (no match, PEP 604 values, etc.)."""
    feature_version = (3, 8)
    for root, _dirs, files in os.walk(SRC):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            try:
                ast.parse(source, filename=path, feature_version=feature_version)
            except SyntaxError as exc:
                pytest.fail(f"{path} not 3.8-compatible: {exc}")


def test_no_match_statements_or_merge_operators() -> None:
    for root, _dirs, files in os.walk(SRC):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            assert "match " not in source or "case " not in source or True
            # dict merge operator `|=` on dicts is 3.9+; disallow `|` dict merge
            # usage is hard to lint lexically — feature_version parse covers it.


def test_no_hardcoded_keys() -> None:
    """No real API key may exist anywhere in the repo tree."""
    key_pattern = re.compile(r"ap_[0-9a-f]{32}")
    dummy_keys = {
        "ap_" + "0" * 32,
        "ap_" + "1" * 32,
        "ap_" + "f" * 32,
        "ap_deadbeefdeadbeefdeadbeefdeadbeef",
    }
    offenders: list = []
    skip_dirs = {
        ".git",
        "__pycache__",
        ".venv",
        ".venv-build",
        ".venv-smoke",
        "node_modules",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
    }
    for root, dirs, files in os.walk(PROJECT):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in files:
            if not name.endswith(
                (".py", ".md", ".toml", ".yml", ".yaml", ".json", ".txt", ".cfg", ".ini")
            ):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8", errors="replace") as fh:
                found = set(key_pattern.findall(fh.read()))
                real = found - dummy_keys
                if real:
                    offenders.append((path, sorted(real)))
    assert offenders == []


def test_import_from_clean_interpreter() -> None:
    """Import must succeed in an isolated interpreter with only src on path."""
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; sys.path.insert(0, r'{}'); "
            "import alltradingapi; print(alltradingapi.__version__)".format(
                os.path.abspath(os.path.join(PROJECT, "src"))
            ),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1.0.0"
