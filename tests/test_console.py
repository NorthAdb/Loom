"""The console's own contract (handoff section 6): four read-only views, guarded
approvals, and no send path.

Every drive is through fastapi.testclient against the real factory over a real
tmp data dir: the views must show the seeded store content, a decision must
reach `loom.console.app.resume_pending` only with a session, a valid CSRF
token, and a same-origin request, and nothing anywhere may offer to send.
"""

import json
import re
from pathlib import Path

from conftest import read_audit
from fastapi.testclient import TestClient

import loom.console.app as console_app
from loom.audit.log import AuditLog
from loom.core.config import Settings
from loom.gateway.pending import PendingApproval, PendingStore
from loom.gateway.scope import ScopeStore
from loom.inbound import InboundMessage, InboxStore
from loom.inbound.accept import compose_triage_prompt
from loom.tools.drafts import DraftStore

BASE_URL = "http://127.0.0.1:8688"
CSRF_INPUT = re.compile(r'name="csrf_token" value="([^"]+)"')


def make_settings(tmp_path):
    return Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")


def make_client(tmp_path, settings, **kwargs):
    app = console_app.create_app(tmp_path, settings, **kwargs)
    return TestClient(app, base_url=BASE_URL), app


def seed_inbox(tmp_path):
    message = InboundMessage(
        message_id="sim-100",
        channel="sim_mail",
        from_header="landlord@example.com",
        subject="Heating bill",
        body="Please pay by the 15th.",
        received_at="2026-11-05T09:00:00+08:00",
        authenticated=True,
    )
    return InboxStore(tmp_path).add(message, task_id="task-1", scope_id="scope-1")


def seed_drafts(tmp_path):
    store = DraftStore(tmp_path)
    first = store.add(to="landlord@example.com", subject="Heating bill", body="We agree and will pay by the 15th.")
    second = store.add(to="cousin@example.net", subject="Weekend plan", body="See you Saturday.")
    return first, second


def seed_pending(tmp_path, pending_id="p-1"):
    return PendingStore(tmp_path).add(
        PendingApproval(
            pending_id=pending_id,
            thread_id="t-1",
            routine_id="daily_tidy",
            tools=[
                {"name": "write_file", "args": {"path": "todo.md", "content": "hi"}},
                {
                    "name": "draft_create_local",
                    "args": {
                        "to": "landlord@example.com",
                        "subject": "Heating bill",
                        "body": "FULL_BODY_UNIQUE_PLEASE_PAY_BY_THE_15TH <script>alert(1)</script>",
                    },
                },
            ],
            reason="approval gate",
            created_at="2026-11-05T09:30:00+08:00",
            status="pending",
        )
    )


def seed_audit(tmp_path):
    AuditLog(tmp_path).append(
        run_id="r-1",
        actor="gateway",
        tool="draft_create_local",
        args={"to": "landlord@example.com", "subject": "Heating bill"},
        decision="allowed",
        correlation_id="corr-1",
    )
    AuditLog(tmp_path).append(
        run_id="r-1",
        actor="runner",
        tool="routine.run",
        args={"routine_id": "daily_tidy"},
        decision="completed",
    )


def csrf_token(page) -> str:
    match = CSRF_INPUT.search(page.text)
    assert match, "the approvals page carries no csrf token input"
    return match.group(1)


def test_the_four_views_render_the_seeded_content(tmp_path):
    inbox_record = seed_inbox(tmp_path)
    first_draft, _second = seed_drafts(tmp_path)
    seed_pending(tmp_path)
    seed_audit(tmp_path)
    client, _app = make_client(tmp_path, make_settings(tmp_path))

    inbox_page = client.get("/inbox")
    assert inbox_page.status_code == 200
    assert "landlord@example.com" in inbox_page.text
    assert "Heating bill" in inbox_page.text
    assert "yes" in inbox_page.text and "task-1" in inbox_page.text

    detail = client.get(f"/inbox/{inbox_record.message.message_id}")
    assert detail.status_code == 200
    assert "Please pay by the 15th." in detail.text
    assert "task-1" in detail.text and "scope-1" in detail.text
    assert client.get("/inbox/no-such-message").status_code == 404

    drafts_page = client.get("/drafts")
    assert drafts_page.status_code == 200
    assert drafts_page.text.count("local only — never sent") == 2
    assert "cousin@example.net" in drafts_page.text
    draft_page = client.get(f"/drafts/{first_draft.draft_id}")
    assert draft_page.status_code == 200
    assert "local only — never sent" in draft_page.text
    assert "We agree and will pay by the 15th." in draft_page.text
    assert client.get("/drafts/no-such-draft").status_code == 404

    approvals_page = client.get("/approvals")
    assert approvals_page.status_code == 200
    assert "p-1" in approvals_page.text
    assert "daily_tidy" in approvals_page.text and "write_file" in approvals_page.text

    audit_page = client.get("/audit")
    assert audit_page.status_code == 200
    assert "gateway" in audit_page.text and "draft_create_local" in audit_page.text
    assert "allowed" in audit_page.text and "corr-1" in audit_page.text and "routine.run" in audit_page.text
    assert audit_page.text.count("<td>-</td>") == 1


def test_the_approvals_page_shows_full_args_and_escapes_them(tmp_path):
    seed_pending(tmp_path)
    client, _app = make_client(tmp_path, make_settings(tmp_path))

    page = client.get("/approvals")

    assert page.status_code == 200
    assert "write_file" in page.text and "draft_create_local" in page.text
    assert "FULL_BODY_UNIQUE_PLEASE_PAY_BY_THE_15TH" in page.text
    assert "landlord@example.com" in page.text and "Heating bill" in page.text
    assert "<script>alert(1)</script>" not in page.text  # escaped, never executable
    assert "\\u003cscript\\u003e" in page.text  # tojson's HTML-safe escape of the angle bracket


def test_the_audit_trace_filter_and_limit_apply(tmp_path):
    seed_audit(tmp_path)
    client, _app = make_client(tmp_path, make_settings(tmp_path))

    traced = client.get("/audit", params={"correlation_id": "corr-1"})
    assert traced.status_code == 200
    assert "draft_create_local" in traced.text
    assert "routine.run" not in traced.text

    untraced = client.get("/audit", params={"correlation_id": "corr-9"})
    assert untraced.status_code == 200 and "No audit rows." in untraced.text

    for _ in range(3):
        seed_audit(tmp_path)
    limited = client.get("/audit", params={"limit": 2})
    assert limited.status_code == 200
    assert limited.text.count("<tr>") == 3  # header row plus two data rows


def test_root_redirects_to_the_inbox(tmp_path):
    client, _app = make_client(tmp_path, make_settings(tmp_path))
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (301, 302, 307)
    assert response.headers["location"] == "/inbox"
    assert client.get("/").url.path == "/inbox"


def test_a_get_sets_the_session_cookie_and_the_form_carries_its_token(tmp_path):
    seed_pending(tmp_path)
    client, _app = make_client(tmp_path, make_settings(tmp_path))
    assert "session" not in client.cookies

    page = client.get("/approvals")
    assert "session" in client.cookies
    token = csrf_token(page)

    recalled = client.get("/approvals")
    assert csrf_token(recalled) == token  # one stable token per session


def test_approve_with_session_and_valid_csrf_calls_resolve_pending_and_redirects(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    seed_inbox(tmp_path)
    seed_pending(tmp_path)
    calls = []

    def fake_resolve(pending_id, decision, resolve_settings, pending_store, mail_transport=None):
        calls.append((pending_id, decision, resolve_settings, pending_store))
        return "completed"

    monkeypatch.setattr(console_app, "resolve_pending", fake_resolve)
    client, _app = make_client(tmp_path, settings)
    token = csrf_token(client.get("/approvals"))

    response = client.post(
        "/approvals/p-1/decide",
        data={"decision": "approve", "csrf_token": token},
        headers={"Origin": BASE_URL},
    )

    assert response.status_code == 200
    assert response.history[0].status_code == 303
    assert response.url.path == "/approvals"
    assert [(call[0], call[1]) for call in calls] == [("p-1", "approve")]
    assert calls[0][2] is settings
    assert calls[0][3].list_pending()[0].pending_id == "p-1"


def test_an_unknown_decision_is_refused_not_500(tmp_path, monkeypatch):
    """The runner refuses decisions outside approve/reject; the console renders that as a refusal."""
    settings = make_settings(tmp_path)
    seed_pending(tmp_path)
    monkeypatch.setattr(
        console_app,
        "resolve_pending",
        lambda pending_id, decision, resolve_settings, pending_store, mail_transport=None: (_ for _ in ()).throw(
            ValueError(f"decision must be 'approve' or 'reject', got {decision!r}")
        ),
    )
    client, _app = make_client(tmp_path, settings)
    token = csrf_token(client.get("/approvals"))

    response = client.post(
        "/approvals/p-1/decide",
        data={"decision": "banana", "csrf_token": token},
        headers={"Origin": BASE_URL},
    )

    assert response.status_code == 403
    assert "decision must be" in response.text


def test_reject_with_session_and_valid_csrf_calls_resolve_pending_and_redirects(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    seed_pending(tmp_path)
    calls = []

    def fake_resolve(pending_id, decision, resolve_settings, pending_store, mail_transport=None):
        calls.append((pending_id, decision))
        return "pending"

    monkeypatch.setattr(console_app, "resolve_pending", fake_resolve)
    client, _app = make_client(tmp_path, settings)
    token = csrf_token(client.get("/approvals"))

    response = client.post(
        "/approvals/p-1/decide",
        data={"decision": "reject", "csrf_token": token},
        headers={"Origin": BASE_URL},
    )

    assert response.status_code == 200
    assert response.url.path == "/approvals"
    assert calls == [("p-1", "reject")]


def test_decide_for_an_unknown_pending_id_is_404(tmp_path):
    seed_pending(tmp_path)
    client, _app = make_client(tmp_path, make_settings(tmp_path))
    token = csrf_token(client.get("/approvals"))

    response = client.post(
        "/approvals/no-such-id/decide",
        data={"decision": "approve", "csrf_token": token},
        headers={"Origin": BASE_URL},
    )

    assert response.status_code == 404


def test_a_post_without_an_origin_from_a_trusted_host_is_allowed(tmp_path, monkeypatch):
    seed_pending(tmp_path)
    calls = []

    def fake_resolve(pending_id, decision, resolve_settings, pending_store, mail_transport=None):
        calls.append(pending_id)
        return "completed"

    monkeypatch.setattr(console_app, "resolve_pending", fake_resolve)
    client, _app = make_client(tmp_path, make_settings(tmp_path))
    token = csrf_token(client.get("/approvals"))

    response = client.post("/approvals/p-1/decide", data={"decision": "approve", "csrf_token": token})

    assert response.status_code == 200
    assert calls == ["p-1"]


def test_approve_without_a_session_is_refused(tmp_path):
    seed_pending(tmp_path)
    client, _app = make_client(tmp_path, make_settings(tmp_path))

    response = client.post(
        "/approvals/p-1/decide",
        data={"decision": "approve", "csrf_token": "forged"},
        headers={"Origin": BASE_URL},
    )

    assert response.status_code == 403


def test_post_with_a_bad_origin_is_refused(tmp_path):
    seed_pending(tmp_path)
    client, _app = make_client(tmp_path, make_settings(tmp_path))
    token = csrf_token(client.get("/approvals"))

    response = client.post(
        "/approvals/p-1/decide",
        data={"decision": "approve", "csrf_token": token},
        headers={"Origin": "http://evil.example"},
    )

    assert response.status_code == 403


def test_post_with_a_bad_host_is_refused(tmp_path):
    seed_pending(tmp_path)
    client, _app = make_client(tmp_path, make_settings(tmp_path))
    token = csrf_token(client.get("/approvals"))

    response = client.post(
        "/approvals/p-1/decide",
        data={"decision": "approve", "csrf_token": token},
        headers={"Host": "evil.example"},
    )

    assert response.status_code == 403


def test_post_with_a_bad_or_missing_csrf_token_is_refused(tmp_path):
    seed_pending(tmp_path)
    client, _app = make_client(tmp_path, make_settings(tmp_path))
    client.get("/approvals")

    forged = client.post(
        "/approvals/p-1/decide",
        data={"decision": "approve", "csrf_token": "forged"},
        headers={"Origin": BASE_URL},
    )
    missing = client.post(
        "/approvals/p-1/decide",
        data={"decision": "approve"},
        headers={"Origin": BASE_URL},
    )

    assert forged.status_code == 403
    assert missing.status_code == 403


def test_there_is_no_send_anywhere(tmp_path):
    seed_drafts(tmp_path)
    client, app = make_client(tmp_path, make_settings(tmp_path))

    paths = [getattr(route, "path", "") for route in app.routes]
    assert paths
    assert not any("send" in path for path in paths)

    template_dir = Path(console_app.__file__).resolve().parent / "templates"
    templates = sorted(template_dir.glob("*.html"))
    assert templates
    for template in templates:
        text = template.read_text(encoding="utf-8").lower()
        assert ">send<" not in text
        assert not any("send" in action for action in re.findall(r'action="([^"]*)"', text))

    assert client.post("/send").status_code == 404
    # /drafts/send falls inside the GET detail route's pattern, so Starlette answers 405; no POST handler exists either way.
    assert client.post("/drafts/send").status_code == 405


def load_mail_sample() -> list[dict]:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "mail" / "sanitized_inbox_sample.json"
    return json.loads(path.read_text(encoding="utf-8"))


class SampleFetchTransport:
    """Serves the sanitized inbox sample the way the read-only IMAP transport would."""

    def __init__(self, rows):
        self.rows = rows

    def list_recent(self, limit: int = 20) -> list[dict]:
        return [
            {key: row[key] for key in ("uid", "from_header", "subject", "date", "seen")}
            for row in self.rows[:limit]
        ]

    def fetch_body(self, uid: int) -> str:
        return next(row["body"] for row in self.rows if row["uid"] == uid)


def test_the_fetch_endpoint_accepts_the_sample_and_a_second_fetch_adds_nothing(tmp_path):
    client, _app = make_client(
        tmp_path, make_settings(tmp_path), fetch_transport=SampleFetchTransport(load_mail_sample())
    )
    token = csrf_token(client.get("/inbox"))

    first = client.post("/inbox/fetch", data={"csrf_token": token}, headers={"Origin": BASE_URL})

    assert first.status_code == 200 and first.url.path == "/inbox"
    page = client.get("/inbox")
    assert "landlord@example.com" in page.text and "stranger@evil.example" in page.text
    assert "imap-102" in page.text
    assert page.text.count("Triage") == 5
    from loom.tasks import TaskStore

    assert len(TaskStore(tmp_path).list()) == 5  # each fetched message created its triage task
    fetched_rows = [row for row in read_audit(tmp_path) if row["tool"] == "mail.fetch"]
    assert fetched_rows and json.loads(fetched_rows[0]["args"])["count"] == 5

    token = csrf_token(client.get("/inbox"))
    second = client.post("/inbox/fetch", data={"csrf_token": token}, headers={"Origin": BASE_URL})

    assert second.status_code == 200
    assert client.get("/inbox").text.count('href="/inbox/imap-') == 5  # dedupe: still five rows
    refetched = [row for row in read_audit(tmp_path) if row["tool"] == "mail.fetch"]
    assert json.loads(refetched[-1]["args"])["count"] == 0


def test_the_fetch_endpoint_refuses_when_mail_is_not_configured(tmp_path):
    client, _app = make_client(tmp_path, make_settings(tmp_path))
    token = csrf_token(client.get("/inbox"))

    response = client.post("/inbox/fetch", data={"csrf_token": token}, headers={"Origin": BASE_URL})

    assert response.status_code == 403
    assert "mail is not configured" in response.text


def test_the_triage_endpoint_redirects_by_outcome(tmp_path, monkeypatch):
    from loom.core.runner import RunOutcome

    settings = make_settings(tmp_path)
    record = seed_inbox(tmp_path)
    calls = []

    def fake_triage(message_id, triage_settings, pending_store):
        calls.append(message_id)
        return RunOutcome("pending", "triage:t-1", pending_id="p-9") if len(calls) == 1 else RunOutcome("completed", "triage:t-1", answer="classified")

    monkeypatch.setattr(console_app, "run_triage", fake_triage)
    client, _app = make_client(tmp_path, settings)
    token = csrf_token(client.get("/inbox"))

    to_approvals = client.post(f"/inbox/{record.message.message_id}/triage", data={"csrf_token": token}, headers={"Origin": BASE_URL})
    assert to_approvals.status_code == 200 and to_approvals.url.path == "/approvals"
    assert to_approvals.history[0].status_code == 303

    to_inbox = client.post(f"/inbox/{record.message.message_id}/triage", data={"csrf_token": token}, headers={"Origin": BASE_URL})
    assert to_inbox.status_code == 200 and to_inbox.url.path == "/inbox"
    assert calls == [record.message.message_id, record.message.message_id]


def test_the_triage_endpoint_renders_the_runner_refusal_as_403(tmp_path):
    seed_inbox(tmp_path)
    client, _app = make_client(tmp_path, make_settings(tmp_path))
    token = csrf_token(client.get("/inbox"))

    response = client.post("/inbox/no-such-message/triage", data={"csrf_token": token}, headers={"Origin": BASE_URL})

    assert response.status_code == 403
    assert "no such inbox message" in response.text


def test_the_mailbox_draft_endpoint_parks_a_pending_with_the_full_draft(tmp_path):
    settings = make_settings(tmp_path).model_copy(update={"imap_host": "imap.example.com"})
    first, _second = seed_drafts(tmp_path)
    client, _app = make_client(tmp_path, settings)
    token = csrf_token(client.get("/inbox"))  # the fetch form renders even with an empty inbox, so it always carries the token

    response = client.post(f"/drafts/{first.draft_id}/mailbox", data={"csrf_token": token}, headers={"Origin": BASE_URL})

    assert response.status_code == 200 and response.url.path == "/approvals"
    page = client.get("/approvals")
    assert "mailbox_draft" in page.text
    assert "landlord@example.com" in page.text and "Heating bill" in page.text
    assert "We agree and will pay by the 15th." in page.text
    record = PendingStore(tmp_path).list_pending()[0]
    assert record.tools[0]["name"] == "mail_create_draft"
    assert record.tools[0]["args"]["to"] == "landlord@example.com"
    assert record.scope_id  # the request minted its own scope


def test_the_mailbox_draft_endpoint_refuses_when_mail_is_not_configured(tmp_path):
    first, _second = seed_drafts(tmp_path)
    client, _app = make_client(tmp_path, make_settings(tmp_path))
    token = csrf_token(client.get("/inbox"))  # the fetch form renders even with an empty inbox, so it always carries the token

    response = client.post(f"/drafts/{first.draft_id}/mailbox", data={"csrf_token": token}, headers={"Origin": BASE_URL})

    assert response.status_code == 403
    assert "mail is not configured" in response.text
    assert PendingStore(tmp_path).list_pending() == []


def test_a_bad_origin_post_to_inbox_fetch_is_refused(tmp_path):
    client, _app = make_client(
        tmp_path, make_settings(tmp_path), fetch_transport=SampleFetchTransport(load_mail_sample())
    )
    client.get("/inbox")

    response = client.post("/inbox/fetch", data={"csrf_token": "whatever"}, headers={"Origin": "http://evil.example"})

    assert response.status_code == 403
    assert client.get("/inbox").text.count('href="/inbox/imap-') == 0


def test_a_post_to_drafts_send_is_405_with_no_side_effect(tmp_path):
    seed_drafts(tmp_path)
    client, app = make_client(tmp_path, make_settings(tmp_path))

    response = client.post("/drafts/send", data={"csrf_token": "x"})

    assert response.status_code == 405
    assert len(DraftStore(tmp_path).list()) == 2  # nothing written, nothing changed
    assert not any(getattr(route, "path", "") == "/drafts/send" for route in app.routes)


def test_fetched_mail_is_never_an_authenticated_owner(tmp_path):
    """P1-3: a From header is not an identity; the fetch click is the control plane's scope, not the sender's."""
    rows = [row for row in load_mail_sample() if row["from_header"] == "stranger@evil.example"]
    client, _app = make_client(tmp_path, make_settings(tmp_path), fetch_transport=SampleFetchTransport(rows))
    token = csrf_token(client.get("/inbox"))

    response = client.post("/inbox/fetch", data={"csrf_token": token}, headers={"Origin": BASE_URL})

    assert response.status_code == 200
    detail = client.get("/inbox/imap-104").text
    assert "no" in detail  # the Authenticated column shows the sender is unverified
    assert "authenticated owner" not in detail
    record = InboxStore(tmp_path).get("imap-104")
    assert record.message.authenticated is False
    assert record.message.from_header == "stranger@evil.example"
    prompt = compose_triage_prompt(record.message)
    assert "authenticated owner" not in prompt and "unauthenticated sender" in prompt
    scope = ScopeStore(tmp_path).get(record.scope_id)
    assert scope.principal == "owner"  # the control plane, never the From header
    assert scope.allowed_actions == frozenset({"draft_create_local"})
    assert scope.memory_write is False
