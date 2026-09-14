"""The one chart on the page: findings per category, stacked by severity.

Severity is a status scale, so the colours are the product's severity palette
(:data:`catia_diff.models.findings.SEVERITY_COLORS`) rather than a categorical
one, and severity is always named as well - legend, segment labels and the
table underneath - so it is never carried by colour alone.
"""

from __future__ import annotations

import plotly.graph_objects as go

from catia_diff.ui import theme
from catia_diff.ui.presenters import CategoryChart

BAR_HEIGHT = 34
CHART_PADDING = 86


def figure_height(chart: CategoryChart) -> int:
    return CHART_PADDING + BAR_HEIGHT * max(1, len(chart.categories))


def category_figure(chart: CategoryChart) -> go.Figure:
    """Horizontal stacked bars, worst-hit category on top."""
    figure = go.Figure()
    for series in chart.series:
        figure.add_bar(
            y=list(chart.categories),
            x=list(series.values),
            name=series.label,
            orientation="h",
            marker={
                "color": series.color,
                # A 2px surface-coloured line keeps stacked segments from
                # bleeding into each other.
                "line": {"color": theme.SURFACE, "width": 2},
                "cornerradius": 4,
            },
            text=[value or "" for value in series.values],
            texttemplate="%{text}",
            textposition="inside",
            insidetextanchor="middle",
            insidetextfont={"color": "#ffffff", "size": 12},
            hovertemplate=f"%{{y}} · {series.label}: %{{x}}<extra></extra>",
        )

    figure.update_layout(
        barmode="stack",
        bargap=0.35,
        height=figure_height(chart),
        margin={"l": 8, "r": 12, "t": 8, "b": 28},
        paper_bgcolor=theme.SURFACE,
        plot_bgcolor=theme.SURFACE,
        font={"family": theme.FONT, "size": 12, "color": theme.INK_SOFT},
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "x": 0,
            "title_text": "",
            # Stacked bars flip the legend by default; severity must read
            # worst-first, the same order as the stack and the table.
            "traceorder": "normal",
            "bgcolor": "rgba(0,0,0,0)",
        },
        hoverlabel={"font_family": theme.FONT},
        # Hide a segment's number rather than shrink it into illegibility.
        uniformtext={"mode": "hide", "minsize": 10},
    )
    figure.update_xaxes(
        showgrid=True,
        gridcolor=theme.GRID,
        zeroline=False,
        tickfont={"color": theme.MUTED},
        rangemode="tozero",
        dtick=1 if _max_total(chart) <= 10 else None,
    )
    figure.update_yaxes(
        autorange="reversed",
        showgrid=False,
        zeroline=False,
        ticksuffix="  ",
        tickfont={"color": theme.INK_SOFT},
    )
    return figure


def _max_total(chart: CategoryChart) -> int:
    if not chart.categories:
        return 0
    return max(
        sum(series.values[index] for series in chart.series)
        for index in range(len(chart.categories))
    )
