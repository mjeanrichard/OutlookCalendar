r"""Run the Tesserae dev server with this repo added as a plugin scan dir.

    <tesserae-clone>\.venv\Scripts\python devserver.py

No symlink, junction or copy into the clone is involved; see _devsupport.py for
how the loader is pointed here. Set TESSERAE_REPO if the clone lives somewhere
other than the default in that module.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _devsupport import ensure_tesserae_importable, install

ensure_tesserae_importable()

from app import plugin_loader  # noqa: E402
from app.main import _serve  # noqa: E402

install(plugin_loader)

if __name__ == "__main__":
    _serve(["--dev", "--host", "127.0.0.1"])
