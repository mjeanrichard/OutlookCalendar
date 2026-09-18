"""The dev scaffolding: pointing Tesserae's loader at this repo.

Note the folder name: every child of the repo root is a candidate plugin, so
non-plugin folders here are prefixed ``_`` (``_tests``, ``_docs``) to keep the
loader from logging "plugin.json missing" for them.

This is the piece that replaces a symlink or junction into the clone, so it
gets tested like anything else: if upstream changes ``plugin_loader.discover``'s
signature, these fail loudly instead of the widgets quietly vanishing.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import plugin_loader

import _devsupport


def test_widget_root_is_the_repo(tmp_path: Path) -> None:
    assert Path(__file__).resolve().parent.parent == _devsupport.WIDGET_ROOT
    assert (_devsupport.WIDGET_ROOT / "outlook_core" / "plugin.json").exists()


def test_the_clone_is_importable() -> None:
    resolved = _devsupport.ensure_tesserae_importable()
    assert (resolved / "app" / "plugin_loader.py").exists()


def test_wrapper_appends_the_repo_and_forwards_everything(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def spy(plugins_dir, *, schema_path, data_root, additional_plugins_dirs=None):
        seen.update(
            plugins_dir=plugins_dir,
            schema_path=schema_path,
            data_root=data_root,
            dirs=additional_plugins_dirs,
        )
        return "registry"

    wrapped = _devsupport.with_widget_root(spy, root=tmp_path)
    result = wrapped(
        Path("plugins"),
        schema_path=Path("schema.json"),
        data_root=Path("data"),
        additional_plugins_dirs=[Path("marketplace")],
    )

    assert result == "registry"
    assert seen["plugins_dir"] == Path("plugins")
    assert seen["schema_path"] == Path("schema.json")
    assert seen["data_root"] == Path("data")
    # The host's own dirs keep their precedence; ours is appended last.
    assert seen["dirs"] == [Path("marketplace"), tmp_path]


def test_wrapper_copes_with_no_additional_dirs(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def spy(plugins_dir, *, schema_path, data_root, additional_plugins_dirs=None):
        captured["dirs"] = additional_plugins_dirs
        return None

    _devsupport.with_widget_root(spy, root=tmp_path)(
        Path("p"), schema_path=Path("s"), data_root=Path("d")
    )

    assert captured["dirs"] == [tmp_path]


def test_wrapper_matches_the_real_discover_signature() -> None:
    """The wrapper is only safe while it mirrors what app_factory calls."""
    expected = inspect.signature(plugin_loader.discover).parameters
    actual = inspect.signature(_devsupport.with_widget_root(plugin_loader.discover)).parameters

    assert set(actual) == set(expected)
    for name, parameter in expected.items():
        assert actual[name].kind == parameter.kind


def test_install_patches_and_hands_back_the_original(tmp_path: Path) -> None:
    module = importlib.import_module("app.plugin_loader")
    original = module.discover
    try:
        returned = _devsupport.install(module, root=tmp_path)

        assert returned is original
        assert module.discover is not original
        assert module.discover.__name__ == "patched"
    finally:
        module.discover = original


def test_devserver_installs_the_patch_on_import() -> None:
    """Importing devserver must be enough; ``_serve`` stays behind __main__."""
    original = plugin_loader.discover
    sys.modules.pop("devserver", None)
    try:
        devserver = importlib.import_module("devserver")

        assert plugin_loader.discover is not original
        assert devserver.plugin_loader is plugin_loader
    finally:
        plugin_loader.discover = original
        sys.modules.pop("devserver", None)


# ----- the fixtures in conftest.py ------------------------------------


def test_the_app_fixture_discovers_this_repos_plugins(app: Flask) -> None:
    registry = app.config["PLUGIN_REGISTRY"]

    assert registry.get("outlook_core") is not None
    assert registry.get("outlook_week") is not None
    # ...without losing the bundled ones the widgets sit next to.
    assert registry.get("weather_now") is not None


def test_no_loader_errors_for_this_repo(app: Flask) -> None:
    ours = {"outlook_core", "outlook_week", "docs", "_docs", "tests", "_tests"}
    problems = [
        (err.plugin_id, err.message)
        for err in app.config["PLUGIN_REGISTRY"].errors
        if err.plugin_id in ours
    ]

    assert problems == []


def test_plugin_data_dirs_are_inside_the_test_tmp_dir(core_dir: Path, tmp_path: Path) -> None:
    assert tmp_path in core_dir.parents


def test_fake_http_refuses_unexpected_hosts(fake_http: Any) -> None:
    import urllib.request

    with pytest.raises(AssertionError, match="unexpected HTTP request"):
        urllib.request.urlopen(urllib.request.Request("https://example.com/"))


def test_fake_http_queues_responses_in_order(fake_http: Any) -> None:
    import json
    import urllib.request

    fake_http.add("example.com", {"n": 1})
    fake_http.add("example.com", {"n": 2})

    first = json.loads(
        urllib.request.urlopen(urllib.request.Request("https://example.com/")).read()
    )
    second = json.loads(
        urllib.request.urlopen(urllib.request.Request("https://example.com/")).read()
    )
    third = json.loads(
        urllib.request.urlopen(urllib.request.Request("https://example.com/")).read()
    )

    assert [first["n"], second["n"], third["n"]] == [1, 2, 2]  # the last one repeats


# ----- keeping the clone current ----------------------------------------


def test_update_runs_every_step_inside_the_clone(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    seen: list[tuple[tuple[str, ...], Path]] = []

    def runner(cmd: Any, cwd: Path) -> int:
        seen.append((tuple(cmd), cwd))
        return 0

    _devsupport.update_clone(tmp_path, runner=runner)

    assert [cmd for cmd, _ in seen] == list(_devsupport.UPDATE_STEPS)
    assert {cwd for _, cwd in seen} == {tmp_path}
    # A fast-forward only: a diverged clone is a person's decision, never ours.
    assert seen[0][0][:3] == ("git", "pull", "--ff-only")


def test_update_stops_at_the_first_failing_step(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    seen: list[tuple[str, ...]] = []

    def runner(cmd: Any, cwd: Path) -> int:
        seen.append(tuple(cmd))
        return 1

    with pytest.raises(SystemExit, match="git pull"):
        _devsupport.update_clone(tmp_path, runner=runner)
    assert len(seen) == 1


def test_update_refuses_a_folder_that_is_not_a_clone(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="TESSERAE_REPO"):
        _devsupport.update_clone(tmp_path, runner=lambda cmd, cwd: 0)


def test_update_steps_use_the_running_interpreter() -> None:
    """The install must land in the venv the server runs from, not whatever
    ``python`` is first on PATH."""
    for cmd in _devsupport.UPDATE_STEPS[1:]:
        assert cmd[0] == sys.executable
