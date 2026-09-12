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
from catia_diff.extract.text_parsing import detect_general_tolerance
from catia_diff.models.drawing import DrawingDocument, Sheet
from catia_diff.models.findings import Category, Evidence, Finding, Severity
from catia_diff.models.geometry import BBox

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

    # -- cached derivations -------------------------------------------------
    def general_tolerance(self, sheet: Sheet) -> str | None:
        if sheet.index not in self._general_tolerance:
            value = sheet.title_block.value("general_tolerance")
            note = detect_general_tolerance(sheet.notes_text())
            self._general_tolerance[sheet.index] = value or note
        return self._general_tolerance[sheet.index]

    def has_general_tolerance(self, sheet: Sheet) -> bool:
        return bool(self.general_tolerance(sheet))

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
            dimensioning,
            gdt,
            symbols,
            title_block,
            tolerancing,
        )
        _LOADED = True


_LOADED = False
_LOAD_LOCK = threading.Lock()
