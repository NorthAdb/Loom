"""The local Operator Console (Gate 7 + Gate 8): a localhost-only skin over the CLI's gates.

`create_app` builds the FastAPI application the `loom console` command serves.
The console owns no business rules: Inbox, Draft, Approval, and Audit read the
same stores the CLI reads; a decision goes through
`loom.core.runner.resolve_pending`, the same runner path `loom approvals`
takes, a fetch goes through the read-only mail transport into the inbound
seam's own accept step, and a mailbox-draft request goes through the
layer-4 control plane in `loom.gateway.mailbox_draft`. The console adds only
its boundary: every POST must survive, in order, the Origin/Host check, the
session CSRF token, and an operator session gained on any GET; refusals are
short plain-text 403s. There is no send route anywhere: drafts are local
only, never sent.
"""

import json
import secrets
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from loom.audit.log import AuditLog
from loom.core.runner import resolve_pending, run_triage
from loom.gateway.mailbox_draft import request_mailbox_draft
from loom.gateway.pending import PENDING, PendingStore
from loom.gateway.scope import ScopeStore
from loom.inbound import InboundMessage, InboxStore, accept_inbound
from loom.ops.routines import now_local
from loom.tasks import TaskStore
from loom.tools.drafts import DraftStore
from loom.tools.mail_fetch import build_fetch_transport
from loom.tools.registry import build_mail_transport

DEFAULT_AUDIT_ROWS = 200
MAX_AUDIT_ROWS = 500

templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")


class Refused(Exception):
    """A POST failed the console's boundary checks; rendered as a plain-text 403."""


def _read_audit(data_dir: Path) -> list[dict]:
    """Every audit row, newest day's file first, lines in write order; read-only."""
    rows: list[dict] = []
    for path in sorted((Path(data_dir) / "audit").glob("*.jsonl"), reverse=True):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _open_session(request: Request) -> dict:
    """Mint the operator session on first GET; every GET of the console is a login."""
    request.session.setdefault("csrf_token", secrets.token_hex(32))
    request.session["operator"] = "local"
    return request.session


SessionDep = Annotated[dict, Depends(_open_session)]


async def _require_csrf(request: Request) -> None:
    """Every POST form must carry the session's csrf token.

    Dependency contract: CSRF runs before the operator check, and a valid
    token can only come from a session — so the operator check below is
    defense in depth, not the sole gate; the two cannot be isolated in a
    test because a valid csrf_token already implies a session exists.
    """
    token = (await request.form()).get("csrf_token")
    if not token or not secrets.compare_digest(str(token), request.session.get("csrf_token") or ""):
        raise Refused("missing or mismatched csrf token")


def _require_operator(request: Request) -> None:
    """Approval posts need an authenticated operator session."""
    if not request.session.get("operator"):
        raise Refused("no operator session")


def create_app(
    data_dir: Path, settings, *, port: int = 8688, secret_key: str | None = None, fetch_transport=None
) -> FastAPI:
    """Build the console bound to one data dir; no module-level app exists.

    `fetch_transport` is the test seam for the fetch button; None means the
    transport is built from settings at request time (and the fetch refuses
    when mail is not configured).
    """
    inbox = InboxStore(data_dir)
    drafts = DraftStore(data_dir)
    pending_store = PendingStore(data_dir)
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=secret_key or secrets.token_hex(32), max_age=3600)

    def render(request: Request, name: str, **context):
        context.setdefault("csrf_token", request.session.get("csrf_token", ""))
        return templates.TemplateResponse(request, name, context)

    @app.middleware("http")
    async def refuse_foreign_posts(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin is not None:
                if origin not in origins:
                    return PlainTextResponse("refused: cross-origin post", status_code=403)
            elif request.headers.get("host") not in hosts:
                return PlainTextResponse("refused: untrusted host", status_code=403)
        return await call_next(request)

    @app.exception_handler(Refused)
    async def refused_response(request: Request, exc: Refused) -> PlainTextResponse:
        return PlainTextResponse(f"refused: {exc}", status_code=403)

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse("/inbox")

    @app.get("/inbox")
    def inbox_view(request: Request, session: SessionDep):
        return render(request, "inbox.html", rows=inbox.list(), csrf_token=session["csrf_token"])

    @app.get("/inbox/{message_id}")
    def inbox_detail(message_id: str, request: Request, session: SessionDep):
        try:
            record = inbox.get(message_id)
        except KeyError:
            raise HTTPException(status_code=404) from None
        return render(request, "inbox_detail.html", record=record, csrf_token=session["csrf_token"])

    @app.post("/inbox/fetch")
    async def inbox_fetch(
        csrf: None = Depends(_require_csrf),
        operator: None = Depends(_require_operator),
    ) -> RedirectResponse:
        transport = fetch_transport or build_fetch_transport(settings)
        if transport is None:
            raise Refused("mail is not configured")
        audit_log = AuditLog(data_dir, settings=settings)
        existing = {record.message.message_id for record in inbox.list()}
        new = 0
        for row in transport.list_recent():
            message_id = f"imap-{row['uid']}"
            if message_id in existing:
                continue
            message = InboundMessage(
                message_id=message_id,
                channel="imap",
                from_header=row["from_header"],
                subject=row["subject"],
                body=transport.fetch_body(row["uid"]),
                received_at=row["date"] or now_local(),
                # A From header is never an identity: mail pulled from the
                # owner's mailbox is still mail from an unverified sender.
                authenticated=False,
            )
            # The fetch click is the control plane acting, so the triage turn
            # gets the minimal draft-only scope even though the sender stays
            # unverified; the scope's principal is the control plane's "owner",
            # never the From header.
            accept_inbound(
                message,
                tasks=TaskStore(data_dir),
                scopes=ScopeStore(data_dir),
                audit=audit_log,
                inbox=inbox,
                control_plane_scope=True,
            )
            new += 1
        audit_log.append(
            run_id=uuid.uuid4().hex,
            actor="console",
            tool="mail.fetch",
            args={"count": new},
            decision="fetched",
        )
        return RedirectResponse("/inbox", status_code=303)

    @app.post("/inbox/{message_id}/triage")
    async def inbox_triage(
        message_id: str,
        csrf: None = Depends(_require_csrf),
        operator: None = Depends(_require_operator),
    ) -> RedirectResponse:
        try:
            outcome = run_triage(message_id, settings, pending_store)
        except ValueError as exc:
            raise Refused(str(exc)) from None
        return RedirectResponse("/approvals" if outcome.outcome == PENDING else "/inbox", status_code=303)

    @app.get("/drafts")
    def drafts_view(request: Request, session: SessionDep):
        # A POST to /drafts/send returns 405: the route table below has GETs
        # only, and no send handler exists anywhere in the codebase.
        return render(request, "drafts.html", rows=drafts.list(), csrf_token=session["csrf_token"])

    @app.get("/drafts/{draft_id}")
    def draft_detail(draft_id: str, request: Request, session: SessionDep):
        try:
            draft = drafts.get(draft_id)
        except KeyError:
            raise HTTPException(status_code=404) from None
        return render(request, "draft_detail.html", draft=draft, csrf_token=session["csrf_token"])

    @app.post("/drafts/{draft_id}/mailbox")
    async def draft_mailbox(
        draft_id: str,
        csrf: None = Depends(_require_csrf),
        operator: None = Depends(_require_operator),
    ) -> RedirectResponse:
        # Request only: nothing touches IMAP until a human approves the parked
        # record, and the resolve path builds the real transport itself.
        try:
            request_mailbox_draft(
                draft_id, settings, pending_store, ScopeStore(data_dir), AuditLog(data_dir, settings=settings), transport=None
            )
        except KeyError:
            raise HTTPException(status_code=404) from None
        except (RuntimeError, ValueError) as exc:
            raise Refused(str(exc)) from None
        return RedirectResponse("/approvals", status_code=303)

    @app.get("/approvals")
    def approvals_view(request: Request, session: SessionDep):
        return render(request, "approvals.html", rows=pending_store.list_pending(), csrf_token=session["csrf_token"])

    @app.post("/approvals/{pending_id}/decide")
    async def decide(
        pending_id: str,
        decision: Annotated[str, Form()],
        csrf: None = Depends(_require_csrf),
        operator: None = Depends(_require_operator),
    ) -> RedirectResponse:
        try:
            resolve_pending(pending_id, decision, settings, pending_store, mail_transport=build_mail_transport(settings))
        except KeyError:
            raise HTTPException(status_code=404) from None
        except ValueError as exc:
            # The runner refuses an unknown decision and an already-resolved
            # record; both are the console's fault, rendered as a refusal.
            raise Refused(str(exc)) from None
        return RedirectResponse("/approvals", status_code=303)

    @app.get("/audit")
    def audit_view(
        request: Request,
        session: SessionDep,
        correlation_id: str | None = None,
        limit: int = DEFAULT_AUDIT_ROWS,
    ):
        rows = _read_audit(data_dir)
        if correlation_id is not None:
            rows = [row for row in rows if row.get("correlation_id") == correlation_id]
        return render(
            request,
            "audit.html",
            rows=rows[: max(0, min(limit, MAX_AUDIT_ROWS))],
            correlation_id=correlation_id,
            csrf_token=session["csrf_token"],
        )

    return app
