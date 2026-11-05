"""The Tool Gateway: the scope check that runs outside the model, before a tool executes.

The model can propose a tool call; this is where the proposal meets the
permission. Every rule failure refuses, and every refusal is audited. The
authorization ledger write is part of the check: if the audit write fails,
the action fails closed — nothing executes without its authorization row.
The graph consults the gateway twice, once at the gate (so a human never
sees a proposal the scope already forbids) and once in run_tools (so an
approval that went stale — an expired scope, a revoked set — still cannot
execute).
"""

import logging
from dataclasses import dataclass

from loom.audit.log import AuditLog
from loom.gateway.scope import RequestScope, ScopeError, ScopeStore

logger = logging.getLogger("loom")


@dataclass(frozen=True)
class GatewayDecision:
    """One check's outcome; a refusal always carries the reason for the audit row."""

    allowed: bool
    reason: str
    scope: RequestScope | None
    needs_approval: bool = False


class ToolGateway:
    """Checks a proposed tool call against the scope named in the graph state.

    `allow_unscoped` is the production posture (open_graph builds it True):
    a run with no scope_id is the operator's own chat or routine and keeps
    the Gate 1-5 human gate without scope checks. Any run that carries a
    scope_id is always fully checked.
    """

    def __init__(self, audit: AuditLog, scopes: ScopeStore, *, allow_unscoped: bool = False) -> None:
        self._audit = audit
        self._scopes = scopes
        self._allow_unscoped = allow_unscoped

    def check(
        self, scope_id: str | None, tool_name: str, *, side_effect: bool, run_id: str, memory_write: bool = False
    ) -> GatewayDecision:
        """Authorize one proposed call, or refuse with the reason audited.

        The order is the fail-closed order: resolve the scope, then expiry,
        then the explicit action set, then the memory-write posture, then the
        approval posture. The audit row is written before the caller may act;
        an audit failure refuses even an otherwise-allowed call. `memory_write`
        is the registry's classification of the proposed action; a scope whose
        `memory_write` is false refuses every memory-writing action.
        """
        scope: RequestScope | None = None
        try:
            if scope_id is None:
                if self._allow_unscoped:
                    return GatewayDecision(
                        allowed=True,
                        reason="unscoped run; the human gate is the boundary",
                        scope=None,
                        needs_approval=side_effect,
                    )
                return self._refuse(None, tool_name, run_id, side_effect, "no scope is attached to this run")
            try:
                scope = self._scopes.get(scope_id)
            except KeyError:
                return self._refuse(None, tool_name, run_id, side_effect, f"no such scope: {scope_id!r}")
            except (OSError, ValueError, TypeError) as exc:
                return self._refuse(None, tool_name, run_id, side_effect, f"scope store unavailable: {exc}")
            try:
                expired = scope.is_expired()
            except ValueError as exc:
                return self._refuse(scope, tool_name, run_id, side_effect, f"scope has an unreadable expiry: {exc}")
            if expired:
                return self._refuse(scope, tool_name, run_id, side_effect, f"scope {scope_id} expired at {scope.expires_at}")
            if not scope.allows(tool_name):
                return self._refuse(
                    scope,
                    tool_name,
                    run_id,
                    side_effect,
                    f"action {tool_name!r} is not in the scope's allowed actions {sorted(scope.allowed_actions)}",
                )
            if memory_write and not scope.memory_write:
                return self._refuse(
                    scope,
                    tool_name,
                    run_id,
                    side_effect,
                    f"action {tool_name!r} writes memory and the scope's memory_write is false",
                )
        except ScopeError as exc:
            return self._refuse(scope, tool_name, run_id, side_effect, f"scope object is illegal: {exc}")

        needs_approval = side_effect and scope.approval_required
        try:
            self._audit.append(
                run_id=run_id,
                actor="gateway",
                tool=tool_name,
                args={"resource": scope.resource, "principal": scope.principal},
                decision="authorized",
                correlation_id=scope.correlation_id,
            )
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("gateway could not write the authorization row; refusing", extra={"error": str(exc)})
            return GatewayDecision(allowed=False, reason=f"audit unavailable, failing closed: {exc}", scope=scope)
        return GatewayDecision(allowed=True, reason="authorized", scope=scope, needs_approval=needs_approval)

    def _refuse(
        self, scope: RequestScope | None, tool_name: str, run_id: str, side_effect: bool, reason: str
    ) -> GatewayDecision:
        """Audit the refusal and return it; the caller must not execute."""
        logger.warning("gateway refused %s: %s", tool_name, reason)
        try:
            self._audit.append(
                run_id=run_id,
                actor="gateway",
                tool=tool_name,
                args={"reason": reason},
                decision="rejected",
                correlation_id=scope.correlation_id if scope else None,
            )
        except OSError as exc:
            logger.warning("gateway could not audit its own refusal", extra={"error": str(exc)})
        return GatewayDecision(allowed=False, reason=reason, scope=scope)
