"""Standards knowledge base: one class per check."""

from catia_diff.rules.base import (
    Rule,
    RuleContext,
    RuleMeta,
    all_rules,
    get_rule,
    register,
    rules_for,
    run_rules,
)

__all__ = [
    "Rule",
    "RuleContext",
    "RuleMeta",
    "all_rules",
    "get_rule",
    "register",
    "rules_for",
    "run_rules",
]
