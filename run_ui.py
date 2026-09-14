"""Start the dashboard with no arguments - for IDEs, where "Run" is one click.

Right-click this file in PyCharm (or VS Code) and run it; the audit dashboard
comes up on http://127.0.0.1:8050.  Everything it does is also available from
the command line::

    catia-diff ui --port 8050 --lang tr

Change the four settings below to pick a different port or language.  The rest
of the file exists so that a plain "Run" works even before ``pip install -e .``:
the package lives under ``src/``, which is not on the path when a file is
executed directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8050
LANGUAGE = "tr"  # "tr" or "en"
PROFILE = "ISO"  # "ISO" or "ASME"


def _ensure_importable() -> None:
    """Put ``src`` on the path when the package is not installed."""
    try:
        import catia_diff  # noqa: F401
    except ModuleNotFoundError:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main() -> int:
    _ensure_importable()
    from catia_diff.errors import CatiaDiffError
    from catia_diff.ui import run

    print(f"catia-diff dashboard: http://{HOST}:{PORT}  (Ctrl+C to stop)")
    try:
        run(host=HOST, port=PORT, language=LANGUAGE, profile=PROFILE)
    except CatiaDiffError as exc:
        # The dashboard extra is not installed: say so instead of a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover - interactive
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
