r"""Run the Tesserae dev server with this repo added as a plugin scan dir.

    <tesserae-clone>\.venv\Scripts\python devserver.py
    <tesserae-clone>\.venv\Scripts\python devserver.py --update   # pull the clone first

``--update`` fast-forwards the Tesserae clone, re-installs it into its venv and
makes sure Playwright's Chromium is present, so the host you develop against is
the one production runs. Anything else on the command line goes to Tesserae.

No symlink, junction or copy into the clone is involved; see _devsupport.py for
how the loader is pointed here. Set TESSERAE_REPO if the clone lives somewhere
other than the default in that module.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _devsupport import ensure_tesserae_importable, install, update_clone

# Flask's reloader re-executes this script in a child process; the pull has
# already happened in the parent by then.
if "--update" in sys.argv and not os.environ.get("WERKZEUG_RUN_MAIN"):
    update_clone()
ensure_tesserae_importable()

from app import plugin_loader  # noqa: E402
from app.main import _serve  # noqa: E402

install(plugin_loader)

if __name__ == "__main__":
    passthrough = [arg for arg in sys.argv[1:] if arg != "--update"]
    _serve(["--dev", "--host", "127.0.0.1", *passthrough])
