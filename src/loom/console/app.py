"""The local Operator Console (Gate 7): a localhost-only skin over the CLI's gates.

`create_app` builds the FastAPI application the `loom console` command serves.
The console owns no business rules: Inbox, Draft, Approval, and Audit read the
same stores the CLI reads, and a decision goes through
`loom.core.runner.resume_pending`, the same runner path `loom approvals`
takes. The console adds only its boundary: every POST must survive, in order,
the Origin/Host check, the session CSRF token, and (for approvals) an operator
session gained on any GET; refusals are short plain-text 403s. There is no
send route anywhere: drafts are local only, never sent.
"""

import json
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from loom.core.runner import resume_pending
from loom.gateway.pending import PendingStore
from loom.inbound import InboxStore
from loom.tools.drafts import DraftStore

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
    """Every POST form must carry the session's csrf token."""
    token = (await request.form()).get("csrf_token")
    if not token or token != request.session.get("csrf_token"):
        raise Refused("missing or mismatched csrf token")


def _require_operator(request: Request) -> None:
    """Approval posts need an authenticated operator session."""
    if not request.session.get("operator"):
        raise Refused("no operator session")


def create_app(data_dir: Path, settings, *, port: int = 8688, secret_key: str | None = None) -> FastAPI:
    """Build the console bound to one data dir; no module-level app exists."""
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

    @app.get("/drafts")
    def drafts_view(request: Request, session: SessionDep):
        return render(request, "drafts.html", rows=drafts.list(), csrf_token=session["csrf_token"])

    @app.get("/drafts/{draft_id}")
    def draft_detail(draft_id: str, request: Request, session: SessionDep):
        try:
            draft = drafts.get(draft_id)
        except KeyError:
            raise HTTPException(status_code=404) from None
        return render(request, "draft_detail.html", draft=draft, csrf_token=session["csrf_token"])

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
            resume_pending(pending_id, decision, settings, pending_store)
        except KeyError:
            raise HTTPException(status_code=404) from None
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
