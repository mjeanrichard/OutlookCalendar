"""Shared development plumbing: point Tesserae at this repo.

Tesserae only scans its own ``plugins/`` plus ``<data_root>/authored`` and
``<data_root>/marketplace``, and exposes no setting for extra directories.
``app_factory`` does call ``plugin_loader.discover(...)`` by module attribute,
though, so replacing that attribute before the app is built injects this folder
as an extra scan dir, covering both startup discovery and the in-process
rediscover the dev reloader uses.

Both ``devserver.py`` and ``conftest.py`` go through here so the dev server and
the test suite load plugins through exactly the same path.

Every child folder of this directory is treated as one plugin (folder name =
plugin id); the loader skips names starting with ``.`` or ``_``, which is why
``_docs`` is spelled that way.

Set TESSERAE_REPO if the clone lives somewhere other than the default below.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

WIDGET_ROOT = Path(__file__).resolve().parent
DEFAULT_TESSERAE_REPO = r"C:\Users\mjean\Documents\Sources\Forks\tesserae"
TESSERAE_REPO = Path(os.environ.get("TESSERAE_REPO", DEFAULT_TESSERAE_REPO))


def ensure_tesserae_importable() -> Path:
    """Put the Tesserae clone on ``sys.path`` and hand back its location.

    A no-op when the clone was pip-installed into the running interpreter,
    which is the normal case (``pip install -e .`` in the clone's venv).
    """
    if str(TESSERAE_REPO) not in sys.path:
        sys.path.insert(0, str(TESSERAE_REPO))
    return TESSERAE_REPO


def with_widget_root(discover: Any, root: Path = WIDGET_ROOT) -> Any:
    """Wrap ``plugin_loader.discover`` so ``root`` is scanned as well."""

    def patched(
        plugins_dir: Any,
        *,
        schema_path: Any,
        data_root: Any,
        additional_plugins_dirs: list[Path] | None = None,
    ) -> Any:
        extra = [*(additional_plugins_dirs or []), root]
        return discover(
            plugins_dir,
            schema_path=schema_path,
            data_root=data_root,
            additional_plugins_dirs=extra,
        )

    return patched


def install(plugin_loader: Any, root: Path = WIDGET_ROOT) -> Any:
    """Patch ``plugin_loader.discover`` in place, returning the original."""
    original = plugin_loader.discover
    plugin_loader.discover = with_widget_root(original, root)
    return original
