import json

from conftest import read_audit

from loom.audit import AuditLog, redact_args, summarize_args


def raw_text(data_dir):
    return next((data_dir / "audit").glob("*.jsonl")).read_text(encoding="utf-8")


def test_append_creates_dated_file_with_one_record(tmp_path):
    log = AuditLog(tmp_path)

    log.append(run_id="r1", actor="human", tool="echo", args={"text": "hi"}, decision="approved")

    records = read_audit(tmp_path)
    assert len(records) == 1
    assert records[0]["decision"] == "approved"
    assert records[0]["actor"] == "human"


def test_sensitive_keys_are_masked_never_logged(tmp_path):
    log = AuditLog(tmp_path)

    log.append(
        run_id="r1",
        actor="loom",
        tool="propose_sandbox_note",
        args={"filename": "a.txt", "api_key": "sk-real-value", "access_token": "tok-123"},
        decision="executed",
    )

    record = read_audit(tmp_path)[0]
    stored = json.loads(record["args"])
    assert stored["api_key"] == "***"
    assert stored["access_token"] == "***"
    assert stored["filename"] == "a.txt"
    raw = raw_text(tmp_path)
    assert "sk-real-value" not in raw
    assert "tok-123" not in raw


def test_long_values_are_capped_and_args_stay_valid_json(tmp_path):
    log = AuditLog(tmp_path)
    long_text = "x" * 2000

    log.append(run_id="r1", actor="loom", tool="echo", args={"text": long_text}, decision="executed")

    record = read_audit(tmp_path)[0]
    stored = json.loads(record["args"])
    assert stored["text"] == "x" * 200


def test_append_caps_each_string_value_and_the_jsonl_line_stays_short(tmp_path):
    log = AuditLog(tmp_path)

    log.append(
        run_id="r1",
        actor="loom",
        tool="mail_create_draft",
        args={"to": "peer@example.com", "subject": "s", "body": "字" * 2500},
        decision="executed",
    )

    record = read_audit(tmp_path)[0]
    stored = json.loads(record["args"])
    assert all(len(value) <= 200 for value in stored.values()), "the cap applies per string value, not per line"
    line = raw_text(tmp_path).strip()
    assert len(line) <= 1200, "capped values keep the whole JSONL line short even for wide alphabets"


def test_records_carry_all_six_fields(tmp_path):
    log = AuditLog(tmp_path)

    log.append(run_id="r1", actor="human", tool="t", args={}, decision="rejected")

    record = read_audit(tmp_path)[0]
    assert set(record) == {"ts", "run_id", "actor", "tool", "args", "decision"}


def test_redact_args_masks_only_sensitive_keys():
    redacted = redact_args({"api_key": "k", "note": "n", "PASSWORD": "p"})
    assert redacted == {"api_key": "***", "note": "n", "PASSWORD": "***"}
    assert summarize_args({"api_key": "k"}) == '{"api_key": "***"}'
