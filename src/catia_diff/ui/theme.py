"""Visual tokens for the dashboard.

One place for every colour, radius and font used by the UI, so the page and the
chart cannot drift apart.  Severity colours are *not* defined here: they come
from :data:`catia_diff.models.findings.SEVERITY_COLORS`, the same constant the
overlay image and the HTML report use.
"""

from __future__ import annotations

#: Page and chart surface.  The severity palette is validated against it.
SURFACE = "#fcfcfb"
CARD = "#ffffff"
INK = "#1d1d1b"
INK_SOFT = "#4a4a46"
MUTED = "#6b6b66"
BORDER = "#e3e2dd"
FAINT = "#a2a19b"
ACCENT = "#12506b"
OK = "#2f7d4f"
GRID = "#eeede8"

FONT = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
)
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

RADIUS = "10px"
SHADOW = "0 1px 2px rgba(29,29,27,0.06), 0 1px 8px rgba(29,29,27,0.04)"
