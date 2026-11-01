"""The read-only loom stats command: zero-safe on an empty data dir, correct over fixtures."""

import json
from datetime import date, timedelta

import loom.cli
from loom.budget import SpendLedger


def test_stats_on_an_empty_data_dir(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path))

    code = loom.cli.main(["stats"])

    out = capsys.readouterr().out
    assert code == 0
    assert "daily budget: unlimited" in out
    assert "today: 0 tokens, runs: chat=0, routine=0" in out
    assert "total: 0 tokens, runs: chat=0, routine=0" in out
    assert "audit: approvals approved=0, rejected=0, tool executions failed=0" in out


def test_stats_counts_spend_and_audit_fixtures(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOOM_DAILY_BUDGET_TOKENS", "1000")
    monkeypatch.setenv("LOOM_TOKEN_PRICE_USD_PER_1K", "0.03")
    ledger = SpendLedger(tmp_path)
    ledger.record("chat", "r1", 400)
    ledger.record("routine", "r2", 300)
    spend = tmp_path / "spend"
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    (spend / f"{yesterday}.jsonl").write_text(
        json.dumps({"ts": "2026-10-31T10:00:00+00:00", "run_id": "r0", "kind": "chat", "tokens": 100}) + "\n"
        + "{not json}\n\n",
        encoding="utf-8",
    )
    audit = tmp_path / "audit"
    audit.mkdir()
    rows = [
        {"ts": "t", "run_id": "a", "actor": "human", "tool": "write_file", "args": "{}", "decision": "approved"},
        {"ts": "t", "run_id": "a", "actor": "loom", "tool": "write_file", "args": "{}", "decision": "executed"},
        {"ts": "t", "run_id": "b", "actor": "human", "tool": "write_file", "args": "{}", "decision": "approved"},
        {"ts": "t", "run_id": "c", "actor": "human", "tool": "delete_file", "args": "{}", "decision": "rejected"},
        {"ts": "t", "run_id": "d", "actor": "loom", "tool": "mail_create_draft", "args": "{}", "decision": "failed"},
    ]
    (audit / "20261101.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows) + "garbage\n", encoding="utf-8"
    )

    code = loom.cli.main(["stats"])

    out = capsys.readouterr().out
    assert code == 0
    assert "daily budget: 1000 tokens, 700 used, 300 left" in out
    assert "today: 700 tokens, $0.02 estimated, runs: chat=1, routine=1" in out
    assert "total: 800 tokens, runs: chat=2, routine=1" in out
    assert "audit: approvals approved=2, rejected=1, tool executions failed=1" in out
