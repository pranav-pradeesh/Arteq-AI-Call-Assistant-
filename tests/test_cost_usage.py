"""Unit tests for the per-service cost model + usage period/limit logic (no DB)."""
from datetime import datetime, timezone

from additions.routes.usage_api import _percent_and_over, billing_period
from src.services import cost_model as cm


def test_cost_breakdown_components_and_total():
    b = cm.call_cost_breakdown(
        duration_s=120, spoken_chars=1000, prompt_tokens=4000, completion_tokens=600
    )
    assert b.stt_paise == 100          # 2 min × 50 paise/min
    assert b.tts_paise == 30           # 1000 chars × 30 paise/1000
    # 4000/1e6*850 + 600/1e6*3400 = 3.4 + 2.04 → round 5
    assert b.llm_paise == 5
    assert b.telephony_paise == 140    # 2 min × 70 paise/min (estimate)
    assert b.cost_paise == 100 + 30 + 5 + 140


def test_cost_breakdown_telephony_override_uses_real_billed():
    b = cm.call_cost_breakdown(
        duration_s=120, spoken_chars=0, telephony_override_paise=63
    )
    assert b.telephony_paise == 63     # real Vobiz CDR cost wins over the estimate
    assert b.cost_paise == b.stt_paise + 63


def test_cost_zero_usage_is_zero():
    b = cm.call_cost_breakdown(duration_s=0, spoken_chars=0)
    assert b.cost_paise == 0


def test_billing_period_calendar_month():
    s, e = billing_period(1, datetime(2026, 6, 22, tzinfo=timezone.utc))
    assert (s.year, s.month, s.day) == (2026, 6, 1)
    assert (e.year, e.month, e.day) == (2026, 7, 1)


def test_billing_period_anchor_before_today_rolls_back():
    # cycle day 15, today is the 10th → period started last month's 15th
    s, e = billing_period(15, datetime(2026, 6, 10, tzinfo=timezone.utc))
    assert (s.month, s.day) == (5, 15)
    assert (e.month, e.day) == (6, 15)


def test_percent_under_and_over_limit():
    assert _percent_and_over(80, 0, 0, 100, None, None) == (80.0, False)
    assert _percent_and_over(120, 0, 0, 100, None, None) == (120.0, True)


def test_percent_uses_max_ratio_across_limits():
    # cost ratio (90/100=0.9) dominates calls ratio (10/100=0.1)
    pct, over = _percent_and_over(10, 0, 90, 100, None, 100)
    assert pct == 90.0 and over is False


def test_no_limits_means_no_percent():
    assert _percent_and_over(5, 5, 5, None, None, None) == (None, False)
