"""Gateway: the permission boundary (Gate 7) and the unattended approval queue.

`gateway.scope` mints and stores Request Scopes (the model can act within one,
never mint or widen one); `gateway.tool_gateway` checks a scope outside the
model before any tool runs and fails closed; `gateway.pending` is the
unattended approval queue behind `loom approvals`.
"""

from loom.gateway.scope import RequestScope, ScopeError, ScopeStore, issue_scope

__all__ = ["RequestScope", "ScopeError", "ScopeStore", "issue_scope"]
