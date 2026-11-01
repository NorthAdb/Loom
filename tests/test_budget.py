"""Budget estimates, the spend ledger, and the brake guard's refusal behavior."""

import json
from datetime import date, datetime

from conftest import read_audit
from langchain_core.messages import AIMessage, HumanMessage

from loom.audit import AuditLog
from loom.budget import (
    UNLIMITED_LINE,
    BudgetGuard,
    SpendLedger,
    estimate_tokens,
    guard_from_settings,
    turn_tokens,
)
from loom.config import Settings


def usage_message(input_tokens, output_tokens):
    return AIMessage(
        content="hi",
        usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens},
    )


def test_estimate_tokens_uses_api_usage_when_present():
    assert estimate_tokens([usage_message(10, 5)]) == 15


def test_estimate_tokens_falls_back_to_chars_over_four():
    assert estimate_tokens([HumanMessage(content="hello world")]) == 3  # 11 // 4 + 1


def test_turn_tokens_counts_from_the_last_human_message_on():
    state = [
        HumanMessage(content="earlier question"),
        AIMessage(content="earlier answer"),
        HumanMessage(content="hello world"),  # 3
        AIMessage(content="hi"),  # 1
    ]
    assert turn_tokens(state) == 4


def test_turn_tokens_honors_usage_metadata_this_turn():
    state = [HumanMessage(content="hello world"), usage_message(10, 5)]
    assert turn_tokens(state) == 18  # 3 for the input + 15 from the API


def test_ledger_round_trip_and_today_total_split(tmp_path):
    past = SpendLedger(tmp_path, today=lambda: date(2026, 10, 30))
    past.record("chat", "r1", 5)
    past.record("routine", "r2", 7)
    now = SpendLedger(tmp_path)
    now.record("chat", "r3", 11)

    assert now.today_tokens() == 11
    assert now.total_tokens() == 23
    assert now.runs_today() == {"chat": 1}
    assert now.runs_total() == {"chat": 2, "routine": 1}


def test_ledger_writes_one_jsonl_file_per_local_day(tmp_path):
    SpendLedger(tmp_path, today=lambda: date(2026, 10, 30)).record("chat", "r1", 5)

    path = tmp_path / "spend" / "2026-10-30.jsonl"
    assert path.exists()
    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert set(entry) == {"ts", "run_id", "kind", "tokens"}
    assert (entry["run_id"], entry["kind"], entry["tokens"]) == ("r1", "chat", 5)
    datetime.fromisoformat(entry["ts"])


def test_ledger_reads_missing_and_corrupt_data_as_zero(tmp_path):
    ledger = SpendLedger(tmp_path)
    assert ledger.today_tokens() == 0
    assert ledger.total_tokens() == 0
    assert ledger.runs_today() == {}
    assert ledger.runs_total() == {}

    spend = tmp_path / "spend"
    spend.mkdir()
    (spend / f"{date.today().isoformat()}.jsonl").write_text("{not json}\n\n", encoding="utf-8")
    assert ledger.today_tokens() == 0


def test_guard_refuses_at_the_limit_and_audits(tmp_path):
    ledger = SpendLedger(tmp_path)
    ledger.record("chat", "seed", 1)
    guard = BudgetGuard(1, ledger, AuditLog(tmp_path))

    assert guard.allow("chat", "r9") is False
    assert guard.allow("routine", "r10") is False

    records = read_audit(tmp_path)
    assert [(r["actor"], r["tool"], r["decision"]) for r in records] == [
        ("loom", "chat", "rejected"),
        ("loom", "routine", "rejected"),
    ]
    args = json.loads(records[0]["args"])
    assert args["reason"] == "daily budget exceeded"
    assert (args["today"], args["budget"]) == (1, 1)


def test_guard_allows_under_the_limit_and_writes_no_audit(tmp_path):
    guard = BudgetGuard(10, SpendLedger(tmp_path), AuditLog(tmp_path))

    assert guard.allow("chat", "r1") is True
    assert not (tmp_path / "audit").exists()


def test_unlimited_guard_refuses_nothing(tmp_path):
    guard = BudgetGuard(0, SpendLedger(tmp_path), AuditLog(tmp_path))

    assert guard.allow("chat", "r1") is True
    assert guard.allow("routine", "r2") is True
    assert guard.limit_line() == UNLIMITED_LINE


def test_limit_line_reports_used_and_left(tmp_path):
    ledger = SpendLedger(tmp_path)
    ledger.record("chat", "r1", 800)

    assert BudgetGuard(1200, ledger, AuditLog(tmp_path)).limit_line() == "daily budget: 1200 tokens, 800 used, 400 left"


def test_guard_from_settings_returns_none_when_unlimited(tmp_path):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path)

    assert guard_from_settings(settings, tmp_path) is None
    assert not (tmp_path / "spend").exists()


def test_guard_from_settings_builds_a_working_guard(tmp_path):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, daily_budget_tokens=100)

    guard = guard_from_settings(settings, tmp_path)

    assert guard is not None
    assert guard.limit_line() == "daily budget: 100 tokens, 0 used, 100 left"
