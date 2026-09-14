"""The one-click launcher: it must work before the package is installed."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / "run_ui.py"


def load_launcher():
    spec = importlib.util.spec_from_file_location("run_ui_under_test", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_launcher_exists_where_the_ide_looks_for_it():
    assert LAUNCHER.is_file()


def test_importing_it_starts_no_server():
    """Loading the module must be inert; only __main__ runs the app."""
    module = load_launcher()
    assert callable(module.main)
    assert (module.HOST, module.PORT) == ("127.0.0.1", 8050)
    assert module.LANGUAGE in {"tr", "en"}
    assert module.PROFILE in {"ISO", "ASME"}


def test_it_works_without_the_package_installed():
    """The point of the launcher: "Run" works from a plain checkout.

    A bare interpreter with no PYTHONPATH cannot see ``src``; after the shim it
    must be able to import the package, or a first-time user's one click fails.
    """
    repo = LAUNCHER.parent
    code = (
        "import importlib.util, sys;"
        f"spec = importlib.util.spec_from_file_location('l', r'{LAUNCHER}');"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
        "m._ensure_importable();"
        "import catia_diff; print(catia_diff.__file__)"
    )
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=repo, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "catia_diff" in result.stdout


def test_a_missing_dashboard_extra_is_reported_not_raised(monkeypatch, capsys):
    """Without the ui extra the launcher explains itself and exits 2."""
    from catia_diff.errors import MissingDependencyError

    module = load_launcher()

    def boom(**_kwargs):
        raise MissingDependencyError("dash", "the web dashboard", extra="ui")

    monkeypatch.setitem(sys.modules, "catia_diff.ui", pytest.importorskip("catia_diff.ui"))
    monkeypatch.setattr("catia_diff.ui.run", boom)

    assert module.main() == 2
    assert "catia-diff[ui]" in capsys.readouterr().err
