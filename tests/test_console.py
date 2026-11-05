"""The console's own contract (handoff section 6): four read-only views, guarded
approvals, and no send path.

Every drive is through fastapi.testclient against the real factory over a real
tmp data dir: the views must show the seeded store content, a decision must
reach `loom.console.app.resume_pending` only with a session, a valid CSRF
token, and a same-origin request, and nothing anywhere may offer to send.
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient

import loom.console.app as console_app
from loom.audit.log import AuditLog
from loom.core.config import Settings
from loom.gateway.pending import PendingApproval, PendingStore
from loom.inbound import InboundMessage, InboxStore
from loom.tools.drafts import DraftStore

BASE_URL = "http://127.0.0.1:8688"
CSRF_INPUT = re.compile(r'name="csrf_token" value="([^"]+)"')


def make_settings(tmp_path):
    return Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")


def make_client(tmp_path, settings):
    app = console_app.create_app(tmp_path, settings)
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


def test_approve_with_session_and_valid_csrf_calls_resume_pending_and_redirects(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    seed_inbox(tmp_path)
    seed_pending(tmp_path)
    calls = []

    def fake_resume(pending_id, decision, resume_settings, pending_store):
        calls.append((pending_id, decision, resume_settings, pending_store))
        return "completed"

    monkeypatch.setattr(console_app, "resume_pending", fake_resume)
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
        "resume_pending",
        lambda pending_id, decision, resume_settings, pending_store: (_ for _ in ()).throw(
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


def test_reject_with_session_and_valid_csrf_calls_resume_pending_and_redirects(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    seed_pending(tmp_path)
    calls = []

    def fake_resume(pending_id, decision, resume_settings, pending_store):
        calls.append((pending_id, decision))
        return "pending"

    monkeypatch.setattr(console_app, "resume_pending", fake_resume)
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

    def fake_resume(pending_id, decision, resume_settings, pending_store):
        calls.append(pending_id)
        return "completed"

    monkeypatch.setattr(console_app, "resume_pending", fake_resume)
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
