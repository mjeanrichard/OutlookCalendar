"""The manifests: schema-valid, and honest about what they reach for.

The capability declarations are the load-bearing part. Upstream applies
``capability_scope()`` in exactly one place, around a widget's ``fetch()``
(``app/composer.py``), so a host contacted during a render must be declared by
the *widget* — even though the HTTP code lives in outlook_core.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import jsonschema
import pytest
from flask import Flask

from _devsupport import TESSERAE_REPO, WIDGET_ROOT

PLUGIN_IDS = ("outlook_core", "outlook_week")


def _manifest(plugin_id: str) -> dict[str, Any]:
    return json.loads((WIDGET_ROOT / plugin_id / "plugin.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("plugin_id", PLUGIN_IDS)
def test_manifest_matches_the_host_schema(plugin_id: str) -> None:
    schema = json.loads(
        (TESSERAE_REPO / "schema" / "plugin.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(_manifest(plugin_id), schema)


@pytest.mark.parametrize("plugin_id", PLUGIN_IDS)
def test_every_host_the_code_calls_is_declared(plugin_id: str, core: Any) -> None:
    declared = {
        entry.split(":", 1)[1]
        for entry in _manifest(plugin_id).get("requires", [])
        if entry.startswith("network:")
    }
    used = {urlsplit(root).hostname for root in (core.GRAPH_ROOT, core.LOGIN_ROOT)}

    assert used <= declared, f"{plugin_id} would be denied at the socket layer"


def test_the_widget_declares_its_settings_dependency() -> None:
    assert "settings:plugin/outlook_core" in _manifest("outlook_week")["requires"]


def test_the_widget_supports_every_cell_size() -> None:
    assert _manifest("outlook_week")["supports"]["sizes"] == ["xs", "sm", "md", "lg"]


def test_no_cell_option_is_named_label() -> None:
    """The host fills a blank ``label`` option with the app-level place name,
    which would title an Outlook cell "Melbourne"."""
    for plugin_id in PLUGIN_IDS:
        names = {option["name"] for option in _manifest(plugin_id).get("cell_options", [])}
        assert "label" not in names
        assert "variant" not in names  # the variant model is gone; styles replace it


def test_the_calendar_picker_is_wired_to_the_cores_choices() -> None:
    options = {o["name"]: o for o in _manifest("outlook_week")["cell_options"]}

    assert options["calendars"]["choices_from"] == "calendars"
    assert options["calendars"]["type"] == "multiselect"


def test_the_core_ships_no_client_js() -> None:
    """kind: "data" plugins are admin-only; a stray client.js would put an
    empty Outlook Core tile in the widget picker."""
    assert _manifest("outlook_core")["kind"] == "data"
    assert not (WIDGET_ROOT / "outlook_core" / "client.js").exists()


def test_both_plugins_load_without_loader_errors(app: Flask) -> None:
    registry = app.config["PLUGIN_REGISTRY"]

    for plugin_id in PLUGIN_IDS:
        plugin = registry.get(plugin_id)
        assert plugin is not None
        assert plugin.server_module is not None
    assert [err for err in registry.errors if err.plugin_id in PLUGIN_IDS] == []


def test_the_core_exposes_an_admin_page(app: Flask) -> None:
    rules = {str(rule) for rule in app.url_map.iter_rules()}

    assert "/plugins/outlook_core/" in rules
    assert "/plugins/outlook_core/signin" in rules


def _code_only(source: str) -> str:
    """Drop comments so prose about fetch() doesn't read as a call to it."""
    without_blocks = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_blocks, flags=re.M)


def test_client_js_exists_for_the_widget() -> None:
    source = (WIDGET_ROOT / "outlook_week" / "client.js").read_text(encoding="utf-8")

    assert "export default function render(shadow, ctx)" in source
    # E-ink rules the renderer can't enforce for us: the screenshot fires as
    # soon as render() resolves, so anything asynchronous or moving is lost.
    code = _code_only(source)
    for forbidden in (
        "requestAnimationFrame",
        "setInterval",
        "setTimeout",
        "fetch(",
        "XMLHttpRequest",
        "@font-face",
        "transition:",
        "animation:",
    ):
        assert forbidden not in code


def test_paths_stay_within_the_repo() -> None:
    for plugin_id in PLUGIN_IDS:
        assert (WIDGET_ROOT / plugin_id / "plugin.json").exists()
        assert (WIDGET_ROOT / plugin_id / "server.py").exists()


def test_docs_and_tests_are_hidden_from_the_loader() -> None:
    """Both are children of a plugin scan root; an unprefixed name would make
    the loader complain about a missing plugin.json on every boot."""
    for folder in ("_docs", "_tests"):
        assert (WIDGET_ROOT / folder).is_dir()
    assert not (WIDGET_ROOT / "docs").exists()
    assert not (WIDGET_ROOT / "tests").exists()


def test_no_stray_plugin_folders(app: Flask) -> None:
    candidates = {
        entry.name
        for entry in WIDGET_ROOT.iterdir()
        if entry.is_dir() and not entry.name.startswith((".", "_"))
    }

    assert candidates == set(PLUGIN_IDS)


def test_versions_are_semver_ish() -> None:
    for plugin_id in PLUGIN_IDS:
        parts = _manifest(plugin_id)["version"].split(".")
        assert len(parts) == 3
        assert all(part.isdigit() for part in parts)


def test_repo_paths_are_relative_to_this_file() -> None:
    assert Path(__file__).resolve().parent.parent == WIDGET_ROOT
