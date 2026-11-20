"""Single source of truth for the installed SDK version.

Mirrored in ``pyproject.toml``; ``tests/test_packaging.py`` asserts both match.
"""

__all__ = ["__version__"]

__version__ = "1.0.1"
