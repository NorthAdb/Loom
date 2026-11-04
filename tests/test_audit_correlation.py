"""Gate 7 rows carry correlation_id; pre-Gate-7 rows stay exactly as they were."""

import json

from loom.audit.log import AuditLog


def _records(data_dir):
    path = next((data_dir / "audit").glob("*.jsonl"))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_rows_without_a_scope_have_no_correlation_id_field(tmp_path):
    AuditLog(tmp_path).append(run_id="r1", actor="loom", tool="write_file", args={"path": "a.txt"}, decision="executed")

    record = _records(tmp_path)[0]
    assert "correlation_id" not in record
    assert set(record) == {"ts", "run_id", "actor", "tool", "args", "decision"}


def test_rows_with_a_scope_carry_the_correlation_id(tmp_path):
    AuditLog(tmp_path).append(
        run_id="r1",
        actor="gateway",
        tool="draft_create_local",
        args={"to": "landlord@example.com"},
        decision="authorized",
        correlation_id="corr-123",
    )

    record = _records(tmp_path)[0]
    assert record["correlation_id"] == "corr-123"
    assert record["decision"] == "authorized"


def test_correlation_id_threads_multiple_rows_for_one_scope(tmp_path):
    audit = AuditLog(tmp_path)
    for decision in ("authorized", "executed"):
        audit.append(
            run_id="r1",
            actor="loom",
            tool="draft_create_local",
            args={"to": "landlord@example.com"},
            decision=decision,
            correlation_id="corr-123",
        )
    audit.append(run_id="r2", actor="loom", tool="echo", args={}, decision="executed")

    records = _records(tmp_path)
    assert [record.get("correlation_id") for record in records[:2]] == ["corr-123", "corr-123"]
    assert records[-1]["run_id"] == "r2" and "correlation_id" not in records[-1]
