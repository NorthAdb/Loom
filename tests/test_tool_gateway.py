"""Behavior contracts for the Tool Gateway's fail-closed checks (ADR-014)."""

import json

import pytest

from loom.audit.log import AuditLog
from loom.gateway import ScopeStore, issue_scope
from loom.gateway.tool_gateway import ToolGateway


@pytest.fixture()
def gateway(tmp_path):
    audit = AuditLog(tmp_path)
    store = ScopeStore(tmp_path)
    return ToolGateway(audit, store), audit, store


def _records(data_dir):
    path = next((data_dir / "audit").glob("*.jsonl"))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_no_scope_at_all_is_refused_and_audited(tmp_path, gateway):
    gw, _audit, _ = gateway

    decision = gw.check(None, "write_file", side_effect=True, run_id="r1")

    assert decision.allowed is False and "no scope" in decision.reason
    record = _records(tmp_path)[0]
    assert record["actor"] == "gateway" and record["decision"] == "rejected"
    assert "correlation_id" not in record


def test_unknown_scope_id_is_refused(tmp_path, gateway):
    gw, _, _ = gateway

    decision = gw.check("missing", "write_file", side_effect=True, run_id="r1")

    assert decision.allowed is False and "no such scope" in decision.reason


def test_expired_scope_is_refused_with_its_correlation_id(tmp_path, gateway):
    gw, _, _ = gateway
    scope = issue_scope(
        "owner", "sim_mail", "sandbox", ["write_file"], expires_at="2020-01-01T00:00:00+08:00", ttl_seconds=None
    )
    ScopeStore(tmp_path).add(scope)

    decision = gw.check(scope.scope_id, "write_file", side_effect=True, run_id="r1")

    assert decision.allowed is False and "expired" in decision.reason
    record = _records(tmp_path)[0]
    assert record["correlation_id"] == scope.correlation_id


def test_action_outside_the_allow_set_is_refused(tmp_path, gateway):
    gw, _, _ = gateway
    scope = issue_scope("owner", "sim_mail", "sandbox", ["echo"], ttl_seconds=None)
    ScopeStore(tmp_path).add(scope)

    decision = gw.check(scope.scope_id, "write_file", side_effect=True, run_id="r1")

    assert decision.allowed is False
    assert "not in the scope's allowed actions" in decision.reason
    assert "'write_file'" in decision.reason and "'echo'" in decision.reason


def test_allowed_action_is_authorized_with_an_audit_row_and_approval_posture(tmp_path, gateway):
    gw, _, _ = gateway
    needing = issue_scope("owner", "sim_mail", "sandbox", ["write_file", "echo"], ttl_seconds=None)
    direct = issue_scope(
        "owner", "sim_mail", "sandbox", ["write_file", "echo"], approval_required=False, ttl_seconds=None
    )
    store = ScopeStore(tmp_path)
    store.add(needing)
    store.add(direct)

    held = gw.check(needing.scope_id, "write_file", side_effect=True, run_id="r1")
    direct_call = gw.check(direct.scope_id, "write_file", side_effect=True, run_id="r2")
    read = gw.check(needing.scope_id, "echo", side_effect=False, run_id="r3")

    assert held.allowed and held.needs_approval is True
    assert direct_call.allowed and direct_call.needs_approval is False
    assert read.allowed and read.needs_approval is False
    decisions = _records(tmp_path)
    assert [record["decision"] for record in decisions] == ["authorized", "authorized", "authorized"]
    assert all(record["actor"] == "gateway" for record in decisions)
    assert decisions[0]["correlation_id"] == needing.correlation_id


def test_audit_failure_fails_closed_even_for_an_allowed_call(tmp_path, gateway, monkeypatch):
    gw, audit, _ = gateway
    scope = issue_scope("owner", "sim_mail", "sandbox", ["write_file"], ttl_seconds=None)
    ScopeStore(tmp_path).add(scope)

    def broken_append(**kwargs):
        raise OSError("audit disk full")

    monkeypatch.setattr(audit, "append", broken_append)
    decision = gw.check(scope.scope_id, "write_file", side_effect=True, run_id="r1")

    assert decision.allowed is False and "failing closed" in decision.reason


def test_a_corrupt_scope_store_fails_closed(tmp_path, gateway):
    gw, _, _ = gateway
    (tmp_path / "scopes.json").write_text("{not json", encoding="utf-8")

    decision = gw.check("whatever", "write_file", side_effect=True, run_id="r1")

    assert decision.allowed is False and "scope store unavailable" in decision.reason
