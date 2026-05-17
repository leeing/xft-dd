"""Tests for state.py reducers: merge_dicts, merge_cost, keep_nonempty_str."""

from __future__ import annotations

from xft.models import CostRecord
from xft.state import keep_nonempty_str, merge_cost, merge_dicts


# ── merge_dicts ───────────────────────────────────────────────────────────────


def test_merge_dicts_basic() -> None:
    """Keys from both dicts appear in the result."""
    result = merge_dicts({"a": 1}, {"b": 2})
    assert result == {"a": 1, "b": 2}


def test_merge_dicts_b_overwrites_a_on_conflict() -> None:
    """When the same key exists in both, b wins."""
    result = merge_dicts({"x": "old"}, {"x": "new"})
    assert result["x"] == "new"


def test_merge_dicts_empty_b() -> None:
    """Merging with an empty b returns a copy of a."""
    result = merge_dicts({"k": 42}, {})
    assert result == {"k": 42}


def test_merge_dicts_empty_a() -> None:
    """Merging into an empty a returns a copy of b."""
    result = merge_dicts({}, {"k": 99})
    assert result == {"k": 99}


def test_merge_dicts_both_empty() -> None:
    """Merging two empty dicts returns an empty dict."""
    assert merge_dicts({}, {}) == {}


# ── merge_cost ────────────────────────────────────────────────────────────────


def test_merge_cost_sums_all_fields() -> None:
    """All CostRecord counters are summed across two branches."""
    a = CostRecord(minimax_search_calls=3, llm_calls=2, llm_tokens_total=500, metaso_calls=1, metaso_credits_total=5)
    b = CostRecord(minimax_search_calls=7, llm_calls=1, llm_tokens_total=200, metaso_calls=2, metaso_credits_total=10)
    result = merge_cost(a, b)
    assert result.minimax_search_calls == 10
    assert result.llm_calls == 3
    assert result.llm_tokens_total == 700
    assert result.metaso_calls == 3
    assert result.metaso_credits_total == 15


def test_merge_cost_with_zeros() -> None:
    """Merging a non-zero record with a zero record returns the non-zero record."""
    a = CostRecord(minimax_search_calls=5, llm_calls=1, llm_tokens_total=100)
    b = CostRecord()
    result = merge_cost(a, b)
    assert result.minimax_search_calls == 5
    assert result.llm_calls == 1
    assert result.llm_tokens_total == 100


def test_merge_cost_both_zero() -> None:
    """Merging two zero records produces a zero record."""
    result = merge_cost(CostRecord(), CostRecord())
    assert result.minimax_search_calls == 0
    assert result.llm_tokens_total == 0


# ── keep_nonempty_str ─────────────────────────────────────────────────────────


def test_keep_nonempty_str_b_wins_when_nonempty() -> None:
    """b is returned when it is non-empty."""
    assert keep_nonempty_str("old", "new") == "new"


def test_keep_nonempty_str_a_kept_when_b_empty() -> None:
    """a is returned when b is an empty string."""
    assert keep_nonempty_str("existing", "") == "existing"


def test_keep_nonempty_str_both_empty() -> None:
    """Both empty → empty string returned."""
    assert keep_nonempty_str("", "") == ""


def test_keep_nonempty_str_a_empty_b_nonempty() -> None:
    """b wins even when a is empty."""
    assert keep_nonempty_str("", "value") == "value"
