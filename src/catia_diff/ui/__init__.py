"""Dash dashboard for the auditor.

``dash`` is an optional dependency, so it is imported lazily: importing
``catia_diff.ui`` (or the presenters and service modules, which are pure Python)
works without it, and only :func:`create_app` / :func:`run` need the package to
be installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from catia_diff.errors import MissingDependencyError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dash import Dash


def _app_module():
    try:
        from catia_diff.ui import app as app_module
    except ImportError as exc:  # pragma: no cover - exercised without dash installed
        raise MissingDependencyError("dash", "the web dashboard", extra="ui") from exc
    return app_module


def create_app(**kwargs: Any) -> Dash:
    """Build the dashboard application."""
    return _app_module().create_app(**kwargs)


def run(**kwargs: Any) -> None:
    """Serve the dashboard (blocking)."""
    _app_module().run(**kwargs)


__all__ = ["create_app", "run"]
