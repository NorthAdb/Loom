"""The read-only fetch transport: the sanitized sample, a fixture-shaped double, and the redaction."""

import json
from pathlib import Path

from loom.core.config import Settings
from loom.tools.mail_fetch import BODY_CHAR_CAP, _body_text, build_fetch_transport, redact_fetch_error

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mail" / "sanitized_inbox_sample.json"


class FakeFetchTransport:
    """Serves the sanitized sample; records which bodies were read."""

    def __init__(self, rows):
        self.rows = rows
        self.bodies_read = []

    def list_recent(self, limit: int = 20) -> "list[dict]":
        return [
            {key: row[key] for key in ("uid", "from_header", "subject", "date", "seen")}
            for row in self.rows[:limit]
        ]

    def fetch_body(self, uid: int) -> str:
        self.bodies_read.append(uid)
        return next(row["body"] for row in self.rows if row["uid"] == uid)


def load_sample() -> "list[dict]":
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_sanitized_sample_is_well_formed_and_synthetic():
    rows = load_sample()

    assert len(rows) == 5
    for row in rows:
        assert set(row) == {"uid", "from_header", "subject", "date", "body", "seen"}
        assert isinstance(row["uid"], int) and isinstance(row["seen"], bool)
        assert "@" in row["from_header"] and "example" in row["from_header"].split("@")[-1]
    bodies = " ".join(row["body"] for row in rows)
    assert "System note to the assistant" in bodies  # the hidden-instruction attempt is in the sample
    assert "you may send now" in bodies  # the send demand is in the sample


def test_fake_transport_over_the_sample_has_the_console_seam_shape():
    rows = load_sample()
    transport = FakeFetchTransport(rows)

    listed = transport.list_recent(limit=3)

    assert [row["uid"] for row in listed] == [row["uid"] for row in rows[:3]]
    assert set(listed[0]) == {"uid", "from_header", "subject", "date", "seen"}
    assert transport.fetch_body(listed[0]["uid"]) == rows[0]["body"]
    assert transport.bodies_read == [rows[0]["uid"]]


def test_redact_fetch_error_surfaces_scheme_host_only():
    leak = "LOGIN failed for owner with the real credentials inside"

    text = redact_fetch_error(RuntimeError(leak), "imap.example.com", 993)

    assert "imaps://imap.example.com:993" in text
    assert leak not in text and "credentials" not in text
    assert text.startswith("RuntimeError")


def test_redact_fetch_error_names_an_unset_host():
    assert "(unset host)" in redact_fetch_error(OSError("no route"), "", 993)


def test_body_cap_is_twenty_thousand_chars():
    assert BODY_CHAR_CAP == 20_000
    assert _body_text(("x" * (BODY_CHAR_CAP + 50)).encode("utf-8")) == "x" * BODY_CHAR_CAP
    assert _body_text("café ☕".encode()).startswith("café")


def test_build_fetch_transport_is_none_without_imap_host(tmp_path):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")

    assert build_fetch_transport(settings) is None
    assert build_fetch_transport(settings.model_copy(update={"imap_host": "imap.example.com"})) is not None
