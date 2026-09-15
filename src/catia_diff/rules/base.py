"""Rule engine.

A rule is a small, independently testable predicate over a sheet (or over the
whole document) that yields :class:`Finding` objects.  Rules declare their
metadata once - id, bilingual title, default severity, category, the standard
clause they implement and the profiles they apply to - and the registry does
selection and ordering.  Adding a check means adding one class; no other file
has to change.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from catia_diff.config import AuditConfig, Profile
from catia_diff.errors import RuleConfigurationError
from catia_diff.extract.text_parsing import detect_blanket_notes, detect_general_tolerance
from catia_diff.models.drawing import DrawingDocument, Sheet
from catia_diff.models.findings import Category, Evidence, Finding, Severity
from catia_diff.models.geometry import BBox
from catia_diff.standards.iso2768 import GeneralToleranceSpec, parse_designation, to_mm

Scope = Literal["sheet", "document"]


@dataclass(frozen=True)
class RuleMeta:
    id: str
    title: str
    title_tr: str
    severity: Severity
    category: Category
    standards: tuple[str, ...] = ()
    profiles: frozenset[Profile] = field(
        default_factory=lambda: frozenset({Profile.ISO, Profile.ASME})
    )
    description: str = ""


class RuleContext:
    """Shared, cached facts a rule may need about the document being audited."""

    def __init__(self, document: DrawingDocument, config: AuditConfig) -> None:
        self.document = document
        self.config = config
        self._general_tolerance: dict[int, str | None] = {}
        self._general_spec: dict[int, GeneralToleranceSpec | None] = {}
        self._coverage: dict[int, list] = {}
        self._alignments: dict[int, list] = {}
        self._markers: dict[int, list] = {}
        self._inherited: dict[int, dict[str, set[str]]] = {}
        self._blanket: dict[int, frozenset[str]] = {}

    # -- cached derivations -------------------------------------------------
    def general_tolerance(self, sheet: Sheet) -> str | None:
        """The general tolerance note as written on the sheet."""
        if sheet.index not in self._general_tolerance:
            value = sheet.title_block.value("general_tolerance")
            note = detect_general_tolerance(sheet.notes_text())
            self._general_tolerance[sheet.index] = value or note
        return self._general_tolerance[sheet.index]

    def has_general_tolerance(self, sheet: Sheet) -> bool:
        return bool(self.general_tolerance(sheet))

    def general_spec(self, sheet: Sheet) -> GeneralToleranceSpec | None:
        """The general tolerance note parsed into ISO 2768 classes.

        ``None`` when the sheet states no note; a spec whose ``is_usable`` is
        False when it states one that names no class (then no number can be
        derived from it).
        """
        if sheet.index not in self._general_spec:
            note = self.general_tolerance(sheet)
            sources = [note, sheet.notes_text(), sheet.title_block.value("general_tolerance")]
            spec = next(
                (parsed for source in sources if (parsed := parse_designation(source))), None
            )
            self._general_spec[sheet.index] = spec
        return self._general_spec[sheet.index]

    def coverage(self, sheet: Sheet):
        """Constraint-graph coverage per view, computed once per sheet.

        Empty when the source carries no measured intervals (PDF, vision): the
        analysis is exact or it does not run.
        """
        if sheet.index not in self._coverage:
            from catia_diff.rules.constraints import sheet_coverage

            self._coverage[sheet.index] = sheet_coverage(sheet, self.config)
        return self._coverage[sheet.index]

    def alignments(self, sheet: Sheet):
        """View pairs that share a measuring direction, computed once per sheet."""
        if sheet.index not in self._alignments:
            from catia_diff.rules.projection import aligned_views

            self._alignments[sheet.index] = aligned_views(sheet, self.config)
        return self._alignments[sheet.index]

    def inherited_axes(self, sheet: Sheet) -> dict[str, set[str]]:
        """Axes a view may leave undimensioned because an aligned view fixes them.

        Orthographic practice dimensions a feature once, in the view that shows
        it best; without this the coverage rules would demand the same
        dimension again in every aligned view.
        """
        if sheet.index not in self._inherited:
            from catia_diff.rules.projection import inherited_axes

            self._inherited[sheet.index] = inherited_axes(sheet, self.coverage(sheet), self.config)
        return self._inherited[sheet.index]

    def axis_inherited(self, sheet: Sheet, view, axis_label: str) -> bool:
        return axis_label in self.inherited_axes(sheet).get(view.id, set())

    def markers(self, sheet: Sheet):
        """Cutting-plane and detail markers drawn on ``sheet``, found once."""
        if sheet.index not in self._markers:
            from catia_diff.rules.markers import detect_markers

            self._markers[sheet.index] = detect_markers(sheet, self.config)
        return self._markers[sheet.index]

    def all_markers(self) -> list[tuple[int, object]]:
        """Every marker in the document, as ``(sheet index, marker)``.

        A section may be cut on one sheet and drawn on another, so resolving a
        reference is a document-wide question.
        """
        return [
            (sheet.index, marker)
            for sheet in self.document.sheets
            for marker in self.markers(sheet)
        ]

    def blanket_notes(self, sheet: Sheet) -> frozenset[str]:
        """Categories a blanket note already covers ("ALL FILLETS R3")."""
        if sheet.index not in self._blanket:
            self._blanket[sheet.index] = detect_blanket_notes(sheet.notes_text())
        return self._blanket[sheet.index]

    def governing_length_mm(self, sheet: Sheet) -> float | None:
        """Largest linear size on the sheet, in mm.

        ISO 2768-2 indexes its tables by the feature's nominal length, which a
        feature control frame does not state.  The largest dimension is the
        conservative stand-in: it yields the widest general tolerance, so a
        rule comparing against it under-reports rather than over-reports.
        """
        sizes = [
            to_mm(dim.nominal, dim.units)
            for dim in sheet.dimensions
            if dim.nominal is not None and dim.units.is_length
        ]
        values = [size for size in sizes if size is not None]
        return max(values) if values else None

    @property
    def profile(self) -> Profile:
        return self.config.profile

    @property
    def is_asme(self) -> bool:
        return self.config.profile is Profile.ASME

    def tolerance_of(self, value: float | None, *, relative: float = 5e-3, floor: float = 1e-3) -> float:
        """Numeric slack used when comparing drawing values."""
        if value is None:
            return floor
        return max(floor, abs(value) * relative)


class Rule(ABC):
    """Base class for every check."""

    meta: ClassVar[RuleMeta]
    scope: ClassVar[Scope] = "sheet"

    # -- API ---------------------------------------------------------------
    @abstractmethod
    def check(self, target: Sheet | DrawingDocument, ctx: RuleContext) -> Iterable[Finding]:
        """Yield findings for ``target``."""

    def applies(self, ctx: RuleContext) -> bool:
        return ctx.profile in self.meta.profiles

    # -- helpers -----------------------------------------------------------
    def finding(
        self,
        *,
        message: str,
        message_tr: str,
        sheet_index: int = 0,
        bbox: BBox | None = None,
        object_ids: Iterable[str] = (),
        suggestion: str | None = None,
        suggestion_tr: str | None = None,
        severity: Severity | None = None,
        confidence: float = 1.0,
        snippet: str | None = None,
        agent: str = "",
    ) -> Finding:
        return Finding(
            rule_id=self.meta.id,
            severity=severity or self.meta.severity,
            category=self.meta.category,
            title=self.meta.title,
            title_tr=self.meta.title_tr,
            message=message,
            message_tr=message_tr,
            suggestion=suggestion,
            suggestion_tr=suggestion_tr,
            standards=self.meta.standards,
            evidence=Evidence(
                sheet_index=sheet_index,
                bbox=bbox,
                object_ids=tuple(object_ids),
                snippet=snippet,
            ),
            confidence=confidence,
            agent=agent or self.meta.category.value,
        )


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------
_REGISTRY: dict[str, Rule] = {}


def register(rule_cls: type[Rule]) -> type[Rule]:
    """Class decorator that instantiates and registers a rule."""
    meta = getattr(rule_cls, "meta", None)
    if meta is None:
        raise RuleConfigurationError(f"{rule_cls.__name__} has no meta")
    if meta.id in _REGISTRY:
        raise RuleConfigurationError(f"duplicate rule id: {meta.id}")
    _REGISTRY[meta.id] = rule_cls()
    return rule_cls


def all_rules() -> list[Rule]:
    _load_rule_modules()
    return sorted(_REGISTRY.values(), key=lambda rule: rule.meta.id)


def get_rule(rule_id: str) -> Rule:
    _load_rule_modules()
    try:
        return _REGISTRY[rule_id]
    except KeyError as exc:
        raise RuleConfigurationError(f"unknown rule: {rule_id}") from exc


def rules_for(config: AuditConfig, *, categories: Iterable[Category] | None = None) -> list[Rule]:
    """Rules enabled by ``config`` (profile, category and id filters)."""
    wanted = set(categories) if categories is not None else None
    selected = []
    for rule in all_rules():
        if wanted is not None and rule.meta.category not in wanted:
            continue
        if config.profile not in rule.meta.profiles:
            continue
        if not config.rule_enabled(rule.meta.id, rule.meta.category):
            continue
        selected.append(rule)
    return selected


def run_rules(
    rules: Iterable[Rule], document: DrawingDocument, ctx: RuleContext
) -> Iterator[Finding]:
    """Execute ``rules``; a failing rule degrades to a warning, never a crash."""
    for rule in rules:
        if not rule.applies(ctx):
            continue
        targets: list[Sheet | DrawingDocument]
        targets = [document] if rule.scope == "document" else list(document.sheets)
        for target in targets:
            try:
                yield from rule.check(target, ctx)
            except Exception as exc:  # pragma: no cover - defensive
                sheet_index = getattr(target, "index", 0)
                yield Finding(
                    rule_id=rule.meta.id,
                    severity=Severity.INFO,
                    category=Category.EXTRACTION,
                    title=f"Rule {rule.meta.id} failed",
                    title_tr=f"{rule.meta.id} kuralı çalışmadı",
                    message=f"{type(exc).__name__}: {exc}",
                    message_tr=f"{type(exc).__name__}: {exc}",
                    evidence=Evidence(sheet_index=sheet_index),
                    agent="rule-engine",
                )


def _load_rule_modules() -> None:
    """Import the rule modules once so their decorators run.

    The checker agents run in parallel, so this has to be atomic: publishing
    the "loaded" flag before the imports finish would let a second thread see
    a half-populated registry and silently skip whole rule families.
    """
    global _LOADED
    if _LOADED:
        return
    with _LOAD_LOCK:
        if _LOADED:
            return
        from catia_diff.rules import (  # noqa: F401
            consistency,
            cross_view,
            dimensioning,
            gdt,
            references,
            symbols,
            title_block,
            tolerancing,
        )
        _LOADED = True


_LOADED = False
_LOAD_LOCK = threading.Lock()
