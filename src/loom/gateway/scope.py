"""Request Scope: the programmatic permission object the model can act within but never mint.

One scope authorizes one principal, on one channel, over one resource, for an
explicit action set, until it expires. The fields are ADR-014's. Two rules are
enforced here, not left to callers: `send_permission` is always false (a scope
constructed with it true is an illegal scope and raises), and the only way a
scope comes into existence is `issue_scope` — the model has no path to it, so
it can propose actions but cannot grant or widen a scope. Identity is not the
input source: a `principal` is issued by the control plane after
authentication, never copied from a From header or a body that claims to be
the owner.
"""

import json
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from loom.ops.routines import write_json_atomic


class ScopeError(ValueError):
    """Raised when a scope would be constructed or used illegally."""


@dataclass(frozen=True)
class RequestScope:
    """One authorization to act; see ADR-014 for the field contract."""

    scope_id: str
    principal: str
    channel: str
    resource: str
    allowed_actions: frozenset[str]
    memory_write: bool
    approval_required: bool
    expires_at: str | None
    correlation_id: str
    send_permission: bool = False

    def __post_init__(self) -> None:
        if self.send_permission:
            raise ScopeError("send_permission is always false; a scope with it true is illegal")
        if not self.principal:
            raise ScopeError("a scope needs a principal authenticated by the control plane")
        if not self.channel or not self.resource:
            raise ScopeError("a scope needs a channel and a resource")
        if not self.allowed_actions:
            raise ScopeError("a scope needs an explicit allowed_actions set")

    def is_expired(self, at: datetime | None = None) -> bool:
        """Whether the scope is past its expires_at; a scope with no expiry never is."""
        if self.expires_at is None:
            return False
        moment = at or datetime.now().astimezone()
        return moment >= datetime.fromisoformat(self.expires_at)

    def allows(self, action: str) -> bool:
        """Whether this action name is in the explicit allow set."""
        return action in self.allowed_actions

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["allowed_actions"] = sorted(self.allowed_actions)
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "RequestScope":
        data = dict(payload)
        data["allowed_actions"] = frozenset(data["allowed_actions"])
        return cls(**data)


def issue_scope(
    principal: str,
    channel: str,
    resource: str,
    allowed_actions: "list[str] | frozenset[str]",
    *,
    memory_write: bool = False,
    approval_required: bool = True,
    expires_at: str | None = None,
    ttl_seconds: int | None = 3600,
    correlation_id: str | None = None,
) -> RequestScope:
    """Mint one scope. The only construction path; callers never build RequestScope directly.

    memory_write defaults false and approval_required defaults true — inbound
    gets no memory write and every side effect waits for a human unless the
    control plane says otherwise. Expiry: an explicit expires_at wins, else
    ttl_seconds from now, else (both None) no expiry. `correlation_id` re-threads
    a replacement scope to the acceptance it replaces; None mints a fresh id.
    """
    if expires_at is None and ttl_seconds is not None:
        expires_at = (datetime.now().astimezone() + timedelta(seconds=ttl_seconds)).isoformat()
    return RequestScope(
        scope_id=uuid.uuid4().hex,
        principal=principal,
        channel=channel,
        resource=resource,
        allowed_actions=frozenset(allowed_actions),
        memory_write=memory_write,
        approval_required=approval_required,
        expires_at=expires_at,
        correlation_id=correlation_id or uuid.uuid4().hex,
    )


class ScopeStore:
    """CRUD over `<data dir>/scopes.json`; writes serialized per process like the other stores."""

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / "scopes.json"
        self._lock = threading.Lock()

    def _load(self) -> "list[RequestScope]":
        if not self._path.exists():
            return []
        items = json.loads(self._path.read_text(encoding="utf-8"))
        return [RequestScope.from_dict(item) for item in items]

    def _write(self, scopes: "list[RequestScope]") -> None:
        write_json_atomic(self._path, [scope.to_dict() for scope in scopes])

    def add(self, scope: RequestScope) -> RequestScope:
        with self._lock:
            scopes = self._load()
            if any(existing.scope_id == scope.scope_id for existing in scopes):
                raise ScopeError(f"scope {scope.scope_id} already exists")
            scopes.append(scope)
            self._write(scopes)
        return scope

    def get(self, scope_id: str) -> RequestScope:
        for scope in self._load():
            if scope.scope_id == scope_id:
                return scope
        raise KeyError(f"no such scope: {scope_id!r}")

    def list(self) -> "list[RequestScope]":
        return self._load()
