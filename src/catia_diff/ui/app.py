"""Dash dashboard: drop a drawing in, read the audit out.

The callbacks here are deliberately thin - every one of them delegates to
:mod:`catia_diff.ui.service` (what to do) or :mod:`catia_diff.ui.presenters`
(what to show), both of which are plain Python and unit-tested.  This module
owns layout and wiring only.
"""

from __future__ import annotations

from typing import Any

from dash import Dash, Input, Output, State, ctx, dash_table, dcc, html, no_update

from catia_diff import __version__
from catia_diff.config import Profile
from catia_diff.models.findings import SEVERITY_COLORS, Category, Severity
from catia_diff.ui import charts, presenters, service, theme
from catia_diff.ui.presenters import t

#: Static labels the language switch has to repaint, as ``id -> text key``.
CHROME: dict[str, str] = {
    "label-title": "title",
    "label-subtitle": "subtitle",
    "label-profile": "profile",
    "label-language": "language",
    "label-gate": "gate",
    "label-drop": "drop",
    "label-filter-severity": "filter_severity",
    "label-filter-category": "filter_category",
    "label-chart": "chart_title",
    "label-downloads": "downloads",
    "sample-btn": "sample",
}

TABLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("number", "col_number"),
    ("rule_id", "col_rule"),
    ("severity", "col_severity"),
    ("category", "col_category"),
    ("sheet", "col_sheet"),
    ("objects", "col_object"),
    ("message", "col_message"),
)

_CARD_STYLE = {
    "background": theme.CARD,
    "border": f"1px solid {theme.BORDER}",
    "borderRadius": theme.RADIUS,
    "boxShadow": theme.SHADOW,
    "padding": "16px 18px",
}
_SECTION_TITLE = {
    "fontSize": "12px",
    "fontWeight": 600,
    "letterSpacing": "0.04em",
    "color": theme.MUTED,
    "margin": "0 0 10px",
}
_HIDDEN = {"display": "none"}


# ---------------------------------------------------------------------------
def create_app(*, language: str = "tr", profile: str = Profile.ISO.value) -> Dash:
    """Build the dashboard.  ``language`` only sets the initial selection."""
    app = Dash(__name__, title="catia-diff", update_title=None)
    app.index_string = _INDEX
    app.layout = _layout(language=language, profile=profile)
    _register(app)
    return app


def run(
    *,
    host: str = "127.0.0.1",
    port: int = 8050,
    debug: bool = False,
    language: str = "tr",
    profile: str = Profile.ISO.value,
) -> None:  # pragma: no cover - a blocking server
    create_app(language=language, profile=profile).run(host=host, port=port, debug=debug)


# ---------------------------------------------------------------------------
def _layout(*, language: str, profile: str) -> html.Div:
    return html.Div(
        [
            dcc.Store(id="store-report"),
            dcc.Store(id="store-overlay"),
            dcc.Store(id="store-rows"),
            dcc.Download(id="download"),
            _header(language, profile),
            html.Main(
                [
                    _intake(language),
                    html.Div(id="results", style=_HIDDEN, children=_results()),
                ],
                style={
                    "maxWidth": "1180px",
                    "margin": "0 auto",
                    "padding": "22px 20px 60px",
                    "display": "flex",
                    "flexDirection": "column",
                    "gap": "18px",
                },
            ),
        ],
        style={
            "background": theme.SURFACE,
            "color": theme.INK,
            "fontFamily": theme.FONT,
            "minHeight": "100vh",
        },
    )


def _header(language: str, profile: str) -> html.Header:
    return html.Header(
        html.Div(
            [
                html.Div(
                    [
                        html.H1(
                            t("title", language),
                            id="label-title",
                            style={"fontSize": "19px", "margin": "0 0 3px", "fontWeight": 600},
                        ),
                        html.P(
                            t("subtitle", language),
                            id="label-subtitle",
                            style={"margin": 0, "fontSize": "13px", "color": theme.MUTED},
                        ),
                        html.P(
                            f"catia-diff {__version__}",
                            style={"margin": "4px 0 0", "fontSize": "11px",
                                   "color": theme.FAINT, "fontFamily": theme.MONO},
                        ),
                    ]
                ),
                html.Div(
                    [
                        _field("label-profile", t("profile", language), "profile",
                               [{"label": p.value, "value": p.value} for p in Profile], profile),
                        _field("label-gate", t("gate", language), "gate-threshold",
                               [], Severity.CRITICAL.value),
                        _field("label-language", t("language", language), "lang",
                               [{"label": "Türkçe", "value": "tr"},
                                {"label": "English", "value": "en"}], language),
                    ],
                    style={"display": "flex", "gap": "12px", "flexWrap": "wrap"},
                ),
            ],
            style={
                "maxWidth": "1180px",
                "margin": "0 auto",
                "padding": "16px 20px",
                "display": "flex",
                "justifyContent": "space-between",
                "alignItems": "flex-end",
                "gap": "18px",
                "flexWrap": "wrap",
            },
        ),
        style={"background": theme.CARD, "borderBottom": f"1px solid {theme.BORDER}"},
    )


def _field(label_id: str, label: str, control_id: str, options: list, value: Any) -> html.Div:
    return html.Div(
        [
            html.Label(label, id=label_id, style={"fontSize": "11px", "color": theme.MUTED}),
            dcc.Dropdown(
                id=control_id,
                options=options,
                value=value,
                clearable=False,
                style={"width": "150px", "fontSize": "13px"},
            ),
        ],
        style={"display": "flex", "flexDirection": "column", "gap": "3px"},
    )


def _intake(language: str) -> html.Section:
    limit_mb = int(service.MAX_UPLOAD_BYTES / 1e6)
    return html.Section(
        [
            dcc.Upload(
                id="upload",
                className="drop",
                max_size=service.MAX_UPLOAD_BYTES,
                children=html.Div(
                    [
                        dcc.Markdown(t("drop", language), id="label-drop", link_target="_blank"),
                        html.Div(
                            t("drop_hint", language, size=limit_mb),
                            id="label-drop-hint",
                            style={"fontSize": "12px", "color": theme.MUTED, "marginTop": "4px"},
                        ),
                    ]
                ),
            ),
            html.Div(
                [
                    html.Button(
                        t("sample", language),
                        id="sample-btn",
                        n_clicks=0,
                        className="ghost",
                        disabled=service.sample_drawing() is None,
                    ),
                    dcc.Loading(
                        html.Div(
                            t("idle", language),
                            id="status",
                            style={"fontSize": "13px", "color": theme.MUTED},
                        ),
                        type="dot",
                        color=theme.ACCENT,
                    ),
                ],
                style={
                    "display": "flex",
                    "alignItems": "center",
                    "gap": "14px",
                    "marginTop": "12px",
                    "flexWrap": "wrap",
                },
            ),
        ],
        style=_CARD_STYLE,
    )


def _results() -> list:
    return [
        html.Div(id="gate", style={"marginBottom": "18px"}),
        html.Div(
            id="cards",
            style={
                "display": "grid",
                "gridTemplateColumns": "repeat(auto-fit, minmax(150px, 1fr))",
                "gap": "12px",
                "marginBottom": "18px",
            },
        ),
        html.Section(
            [
                html.H2(t("chart_title"), id="label-chart", style=_SECTION_TITLE),
                dcc.Graph(id="chart", config={"displayModeBar": False}),
            ],
            style={**_CARD_STYLE, "marginBottom": "18px"},
        ),
        html.Section(
            [
                html.Div(
                    [
                        _filter("label-filter-severity", t("filter_severity"), "filter-severity"),
                        _filter("label-filter-category", t("filter_category"), "filter-category"),
                    ],
                    style={"display": "flex", "gap": "14px", "flexWrap": "wrap",
                           "marginBottom": "14px"},
                ),
                dcc.Tabs(
                    id="tabs",
                    value="findings",
                    children=[
                        dcc.Tab(label=t("tab_findings"), value="findings", id="tab-findings"),
                        dcc.Tab(label=t("tab_overlay"), value="overlay", id="tab-overlay"),
                        dcc.Tab(label=t("tab_document"), value="document", id="tab-document"),
                    ],
                ),
                html.Div(id="panel-findings", children=[_table(), html.Div(id="detail")]),
                html.Div(id="panel-overlay", style=_HIDDEN, children=html.Div(id="overlay-box")),
                html.Div(
                    id="panel-document",
                    style=_HIDDEN,
                    children=[html.Div(id="stats"), html.Div(id="warnings")],
                ),
            ],
            style=_CARD_STYLE,
        ),
        html.Section(
            [
                html.H2(t("downloads"), id="label-downloads", style=_SECTION_TITLE),
                html.Div(
                    [
                        html.Button("JSON", id="dl-json", n_clicks=0, className="ghost"),
                        html.Button("Markdown", id="dl-md", n_clicks=0, className="ghost"),
                        html.Button("HTML", id="dl-html", n_clicks=0, className="ghost"),
                    ],
                    style={"display": "flex", "gap": "10px", "flexWrap": "wrap"},
                ),
            ],
            style={**_CARD_STYLE, "marginTop": "18px"},
        ),
    ]


def _filter(label_id: str, label: str, control_id: str) -> html.Div:
    return html.Div(
        [
            html.Label(label, id=label_id, style={"fontSize": "11px", "color": theme.MUTED}),
            dcc.Dropdown(
                id=control_id,
                options=[],
                value=[],
                multi=True,
                placeholder=t("filter_all"),
                style={"minWidth": "230px", "fontSize": "13px"},
            ),
        ],
        style={"display": "flex", "flexDirection": "column", "gap": "3px"},
    )


#: Dash paints the active cell red by default, which would read as a severity
#: here; a neutral tint keeps the colour vocabulary honest.
_TABLE_CONDITIONS: list[dict] = [
    {"if": {"state": "active"}, "backgroundColor": "#eef3f6",
     "border": f"1px solid {theme.BORDER}"},
    {"if": {"state": "selected"}, "backgroundColor": "#eef3f6",
     "border": f"1px solid {theme.BORDER}"},
] + [
    {
        "if": {"filter_query": f'{{severity_key}} = "{severity.value}"',
               "column_id": "severity"},
        "color": color,
        "fontWeight": 600,
    }
    for severity, color in SEVERITY_COLORS.items()
]


def _table() -> dash_table.DataTable:
    return dash_table.DataTable(
        id="table",
        columns=[{"name": t(key), "id": column} for column, key in TABLE_COLUMNS],
        data=[],
        page_size=15,
        sort_action="native",
        cell_selectable=True,
        style_as_list_view=True,
        style_table={"overflowX": "auto", "marginTop": "12px"},
        style_cell={
            "fontFamily": theme.FONT,
            "fontSize": "13px",
            "textAlign": "left",
            "padding": "9px 10px",
            "border": "none",
            "borderBottom": f"1px solid {theme.BORDER}",
            "maxWidth": "520px",
            "whiteSpace": "normal",
            "height": "auto",
        },
        style_header={
            "fontWeight": 600,
            "fontSize": "11px",
            "letterSpacing": "0.04em",
            "color": theme.MUTED,
            "background": theme.SURFACE,
            "borderBottom": f"1px solid {theme.BORDER}",
        },
        # Fixed narrow columns and one wide message column: on a small screen
        # the table scrolls sideways instead of growing rows out of the page.
        style_cell_conditional=[
            {"if": {"column_id": "number"}, "width": "46px", "minWidth": "46px",
             "fontFamily": theme.MONO},
            {"if": {"column_id": "rule_id"}, "width": "84px", "minWidth": "84px",
             "fontFamily": theme.MONO},
            {"if": {"column_id": "severity"}, "width": "86px", "minWidth": "86px"},
            {"if": {"column_id": "category"}, "width": "130px", "minWidth": "110px"},
            {"if": {"column_id": "sheet"}, "width": "58px", "minWidth": "58px"},
            {"if": {"column_id": "objects"}, "width": "150px", "minWidth": "120px"},
            {"if": {"column_id": "message"}, "minWidth": "330px"},
        ],
        style_data_conditional=_TABLE_CONDITIONS,
    )


# ---------------------------------------------------------------------------
def _register(app: Dash) -> None:
    """Wire the views below to component properties.

    Each callback is one line on purpose: the work lives in the ``*_view``
    functions, which take plain values and can be called straight from a test.
    """

    app.callback(
        Output("store-report", "data"),
        Output("store-overlay", "data"),
        Output("status", "children"),
        Output("results", "style"),
        Input("upload", "contents"),
        Input("sample-btn", "n_clicks"),
        State("upload", "filename"),
        State("profile", "value"),
        State("lang", "value"),
        prevent_initial_call=True,
    )(lambda contents, _clicks, filename, profile, lang: audit_view(
        ctx.triggered_id, contents, filename, profile, lang
    ))

    app.callback(
        Output("cards", "children"),
        Output("chart", "figure"),
        Output("chart", "style"),
        Output("table", "data"),
        Output("store-rows", "data"),
        Output("gate", "children"),
        Output("overlay-box", "children"),
        Output("stats", "children"),
        Output("warnings", "children"),
        Input("store-report", "data"),
        Input("filter-severity", "value"),
        Input("filter-category", "value"),
        Input("lang", "value"),
        Input("gate-threshold", "value"),
        State("store-overlay", "data"),
    )(results_view)

    app.callback(
        Output("detail", "children"),
        Input("table", "active_cell"),
        Input("lang", "value"),
        State("store-report", "data"),
        State("store-rows", "data"),
    )(detail_view)

    app.callback(
        Output("filter-severity", "options"),
        Output("gate-threshold", "options"),
        Output("filter-category", "options"),
        Output("filter-severity", "placeholder"),
        Output("filter-category", "placeholder"),
        Output("table", "columns"),
        Output("tab-findings", "label"),
        Output("tab-overlay", "label"),
        Output("tab-document", "label"),
        Output("label-drop-hint", "children"),
        *[Output(element_id, "children") for element_id in CHROME],
        Input("lang", "value"),
    )(chrome_view)

    app.callback(
        Output("gate", "style"),
        Output("panel-findings", "style"),
        Output("panel-overlay", "style"),
        Output("panel-document", "style"),
        Input("tabs", "value"),
    )(panel_view)

    app.callback(
        Output("download", "data"),
        Input("dl-json", "n_clicks"),
        Input("dl-md", "n_clicks"),
        Input("dl-html", "n_clicks"),
        State("store-report", "data"),
        State("lang", "value"),
        prevent_initial_call=True,
    )(lambda _json, _md, _html, payload, lang: download_view(ctx.triggered_id, payload, lang))


# ---------------------------------------------------------------------------
# Views: plain functions over plain values, one per callback.
def audit_view(triggered_id, contents, filename, profile, lang) -> tuple:
    """Run one audit and hand the screen its report, overlay and status line."""
    sample = triggered_id == "sample-btn"
    outcome = service.run_audit(
        contents=None if sample else contents,
        filename=None if sample else filename,
        path=service.sample_drawing() if sample else None,
        profile=profile,
        language=lang,
    )
    style = {} if outcome.report is not None else _HIDDEN
    return outcome.report_json, outcome.overlay_uri, _status(outcome), style


def results_view(payload, severities, categories, lang, threshold, overlay) -> tuple:
    """Everything that is derived from the stored report and the filters."""
    report = service.parse_report(payload)
    if report is None:
        empty = charts.category_figure(presenters.CategoryChart((), ()))
        return [], empty, _HIDDEN, [], [], None, None, None, None

    rows = presenters.finding_rows(
        report,
        lang,
        severities=[Severity(value) for value in severities] if severities else None,
        categories=[Category(value) for value in categories] if categories else None,
    )
    chart = presenters.category_chart(rows, lang)
    return (
        [_card(card) for card in presenters.severity_cards(report, lang)],
        charts.category_figure(chart),
        _HIDDEN if chart.is_empty else {},
        [row.as_table_row() for row in rows],
        [row.id for row in rows],
        _gate(presenters.gate_status(report, Severity(threshold), lang)),
        _overlay(overlay, lang),
        _stats(presenters.document_stats(report, lang), lang),
        _warnings(report.warnings, lang),
    )


def detail_view(active_cell, lang, payload, row_ids):
    """The panel under the table: one finding in full."""
    report = service.parse_report(payload)
    if report is None or not active_cell or not row_ids:
        return html.P(t("detail_pick", lang), style={"color": theme.MUTED, "fontSize": "13px"})
    index = active_cell.get("row", 0)
    if index >= len(row_ids):
        return no_update
    row = presenters.row_by_id(presenters.finding_rows(report, lang), row_ids[index])
    return _detail_panel(row, lang) if row else no_update


def chrome_view(lang) -> tuple:
    """Static labels, filter options and table headers in the chosen language."""
    severities = [{"label": severity.label(lang), "value": severity.value} for severity in Severity]
    return (
        severities,
        severities,
        [{"label": category.label(lang), "value": category.value} for category in Category],
        t("filter_all", lang),
        t("filter_all", lang),
        [{"name": t(key, lang), "id": column} for column, key in TABLE_COLUMNS],
        t("tab_findings", lang),
        t("tab_overlay", lang),
        t("tab_document", lang),
        t("drop_hint", lang, size=int(service.MAX_UPLOAD_BYTES / 1e6)),
        *[t(key, lang) for key in CHROME.values()],
    )


def panel_view(active: str) -> tuple:
    shown = {"paddingTop": "4px"}
    return (
        {"marginBottom": "18px"},
        shown if active == "findings" else _HIDDEN,
        shown if active == "overlay" else _HIDDEN,
        shown if active == "document" else _HIDDEN,
    )


def download_view(triggered_id, payload, lang):
    report = service.parse_report(payload)
    if report is None:
        return no_update
    fmt = {"dl-json": "json", "dl-md": "md", "dl-html": "html"}[triggered_id]
    filename, content, mime = service.report_download(report, fmt, lang)
    return {"content": content, "filename": filename, "type": mime}


# ---------------------------------------------------------------------------
def _status(outcome: service.AuditOutcome) -> html.Span:
    """The file that was read, or why it could not be.

    The counts live in the cards and the gate banner, which re-render in the
    selected language; repeating them here would freeze the wording of whatever
    language the audit happened to run in.
    """
    if outcome.ok and outcome.report is not None:
        return html.Span(
            outcome.report.document.name,
            style={"fontSize": "13px", "color": theme.MUTED, "fontFamily": theme.MONO},
        )
    return html.Span(
        f"⚠ {outcome.message}",
        style={"fontSize": "13px", "color": theme.INK, "fontWeight": 600},
    )


def _card(card: presenters.SeverityCard) -> html.Div:
    return html.Div(
        [
            html.Div(
                card.label,
                style={"fontSize": "12px", "color": theme.MUTED, "marginBottom": "2px"},
            ),
            html.Div(
                str(card.count),
                style={
                    "fontSize": "28px",
                    "fontWeight": 600,
                    "lineHeight": 1.1,
                    "color": card.color if card.count else theme.BORDER,
                },
            ),
        ],
        style={**_CARD_STYLE, "padding": "12px 14px",
               "borderLeft": f"4px solid {card.color if card.count else theme.BORDER}"},
    )


def _gate(status: presenters.GateStatus) -> html.Div:
    color = theme.OK if status.ok else SEVERITY_COLORS[Severity.CRITICAL]
    return html.Div(
        [
            html.Span("●", style={"color": color, "marginRight": "8px"}),
            html.Span(status.text, style={"fontSize": "13px", "color": theme.INK_SOFT}),
        ],
        style={
            "background": theme.CARD,
            "border": f"1px solid {theme.BORDER}",
            "borderLeft": f"4px solid {color}",
            "borderRadius": theme.RADIUS,
            "padding": "11px 14px",
        },
    )


def _overlay(uri: str | None, lang: str):
    if not uri:
        return html.P(t("no_overlay", lang), style={"color": theme.MUTED, "fontSize": "13px"})
    return html.Img(
        src=uri,
        style={"width": "100%", "border": f"1px solid {theme.BORDER}",
               "borderRadius": "6px", "marginTop": "12px"},
    )


def _stats(rows: list[tuple[str, str]], lang: str) -> html.Div:
    return html.Div(
        [
            html.H3(t("stats", lang), style=_SECTION_TITLE),
            html.Div(
                [
                    html.Div(
                        [
                            html.Span(label, style={"color": theme.MUTED, "fontSize": "12px"}),
                            html.Span(value, style={"fontFamily": theme.MONO, "fontSize": "13px"}),
                        ],
                        style={"display": "flex", "justifyContent": "space-between",
                               "borderBottom": f"1px solid {theme.BORDER}", "padding": "6px 0"},
                    )
                    for label, value in rows
                ],
                style={"display": "grid", "gridTemplateColumns":
                       "repeat(auto-fit, minmax(220px, 1fr))", "gap": "0 26px"},
            ),
        ],
        style={"marginTop": "12px"},
    )


def _warnings(warnings: list[str], lang: str):
    if not warnings:
        return None
    return html.Div(
        [
            html.H3(t("warnings", lang), style=_SECTION_TITLE),
            html.Ul(
                [html.Li(item, style={"fontSize": "13px", "color": theme.INK_SOFT})
                 for item in warnings]
            ),
        ],
        style={"marginTop": "18px"},
    )


def _detail_panel(row: presenters.FindingRow, lang: str) -> html.Div:
    items: list = [
        html.Div(
            [
                html.Span(
                    row.severity_label,
                    style={"background": row.color, "color": "#fff", "borderRadius": "4px",
                           "padding": "2px 8px", "fontSize": "11px", "fontWeight": 600},
                ),
                html.Span(row.rule_id, style={"fontFamily": theme.MONO, "marginLeft": "10px",
                                              "color": theme.MUTED, "fontSize": "12px"}),
            ]
        ),
        html.H3(row.title, style={"margin": "10px 0 6px", "fontSize": "15px"}),
        _detail_row(t("detail_message", lang), row.message),
    ]
    if row.suggestion:
        items.append(_detail_row(t("detail_suggestion", lang), row.suggestion))
    if row.standards:
        items.append(_detail_row(t("detail_standard", lang), row.standards))
    if row.objects:
        items.append(_detail_row(t("detail_location", lang), f"{row.sheet + 1} · {row.objects}"))
    if row.number is not None:
        items.append(_detail_row(t("detail_box", lang), f"#{row.number}"))
    items.append(_detail_row(t("detail_confidence", lang), f"{row.confidence:.0%}"))
    return html.Div(
        items,
        style={**_CARD_STYLE, "background": theme.SURFACE, "marginTop": "14px",
               "borderLeft": f"4px solid {row.color}"},
    )


def _detail_row(label: str, value: str) -> html.Div:
    return html.Div(
        [
            html.Div(label, style={"fontSize": "11px", "color": theme.MUTED,
                                   "letterSpacing": "0.04em"}),
            html.Div(value, style={"fontSize": "13px", "color": theme.INK, "marginTop": "2px"}),
        ],
        style={"marginTop": "10px"},
    )


_INDEX = """<!DOCTYPE html>
<html>
<head>
  {%metas%}<title>{%title%}</title>{%favicon%}{%css%}
  <style>
    body { margin: 0; background: SURFACE; }
    .drop {
      border: 1.5px dashed BORDER; border-radius: RADIUS; padding: 26px;
      text-align: center; cursor: pointer; background: SURFACE; transition: border-color .15s;
    }
    .drop:hover { border-color: ACCENT; }
    .drop p { margin: 0; font-size: 14px; }
    button.ghost {
      font: inherit; font-size: 13px; padding: 7px 14px; cursor: pointer;
      background: #fff; color: INK; border: 1px solid BORDER; border-radius: 7px;
    }
    button.ghost:hover:enabled { border-color: ACCENT; color: ACCENT; }
    button.ghost:disabled { opacity: .45; cursor: not-allowed; }
    .dash-table-container .row { margin: 0; }
  </style>
</head>
<body>{%app_entry%}<footer>{%config%}{%scripts%}{%renderer%}</footer></body>
</html>
""".replace("SURFACE", theme.SURFACE).replace("BORDER", theme.BORDER).replace(
    "ACCENT", theme.ACCENT
).replace("RADIUS", theme.RADIUS).replace("INK", theme.INK)


__all__ = ["create_app", "run"]
