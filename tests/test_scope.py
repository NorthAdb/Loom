"""Behavior contracts for the Request Scope object and its store (ADR-014)."""

import pytest

from loom.gateway import RequestScope, ScopeError, ScopeStore, issue_scope


def _scope(**overrides):
    defaults = dict(
        scope_id="s1",
        principal="owner",
        channel="sim_mail",
        resource="mail/drafts:landlord",
        allowed_actions=frozenset({"create_draft"}),
        memory_write=False,
        approval_required=True,
        expires_at=None,
        correlation_id="c1",
    )
    defaults.update(overrides)
    return RequestScope(**defaults)


def test_scope_carries_the_adr014_fields():
    scope = _scope()
    assert scope.scope_id == "s1"
    assert scope.principal == "owner"
    assert scope.channel == "sim_mail"
    assert scope.resource == "mail/drafts:landlord"
    assert scope.allowed_actions == {"create_draft"}
    assert scope.memory_write is False
    assert scope.approval_required is True
    assert scope.correlation_id == "c1"
    assert scope.send_permission is False


def test_send_permission_true_is_an_illegal_scope_everywhere():
    with pytest.raises(ScopeError, match="always false"):
        _scope(send_permission=True)


def test_scope_refuses_empty_principal_channel_resource_or_actions():
    with pytest.raises(ScopeError, match="principal"):
        _scope(principal="")
    with pytest.raises(ScopeError, match="channel and a resource"):
        _scope(channel="")
    with pytest.raises(ScopeError, match="channel and a resource"):
        _scope(resource="")
    with pytest.raises(ScopeError, match="allowed_actions"):
        _scope(allowed_actions=frozenset())


def test_expiry_is_checked_against_a_moment_not_wall_luck():
    from datetime import datetime

    scope = _scope(expires_at="2026-01-01T00:00:00+08:00")
    never = _scope(expires_at=None)
    assert scope.is_expired(at=datetime.fromisoformat("2026-06-01T00:00:00+08:00"))
    assert not scope.is_expired(at=datetime.fromisoformat("2025-06-01T00:00:00+08:00"))
    assert never.is_expired(at=datetime.fromisoformat("2099-01-01T00:00:00+08:00")) is False


def test_allows_reads_only_the_explicit_set():
    scope = _scope()
    assert scope.allows("create_draft")
    assert not scope.allows("send_mail")
    assert not scope.allows("memory_write")


def test_issue_scope_is_the_only_mint_and_defaults_are_fail_safe():
    scope = issue_scope("owner", "cli", "sandbox/notes", ["write_note"])

    assert scope.send_permission is False
    assert scope.memory_write is False
    assert scope.approval_required is True
    assert scope.scope_id and scope.correlation_id
    assert scope.expires_at is not None  # default ttl keeps scopes short-lived
    assert scope.allows("write_note") and scope.allowed_actions == frozenset({"write_note"})


def test_issue_scope_honors_explicit_expiry_and_memory_write():
    scope = issue_scope(
        "owner",
        "sim_mail",
        "mail/drafts",
        ["create_draft"],
        memory_write=False,
        approval_required=True,
        expires_at="2030-01-01T00:00:00+08:00",
        ttl_seconds=None,
    )
    assert scope.expires_at == "2030-01-01T00:00:00+08:00"
    assert scope.memory_write is False


def test_store_round_trip_keeps_the_frozenset_and_every_field(tmp_path):
    store = ScopeStore(tmp_path)
    scope = issue_scope("owner", "sim_mail", "mail/drafts", {"create_draft", "classify"}, expires_at=None, ttl_seconds=None)
    store.add(scope)

    loaded = ScopeStore(tmp_path).get(scope.scope_id)
    assert loaded == scope
    assert loaded.allowed_actions == {"create_draft", "classify"}


def test_store_refuses_duplicate_scope_ids_and_unknown_ids(tmp_path):
    store = ScopeStore(tmp_path)
    scope = issue_scope("owner", "cli", "sandbox/notes", ["write_note"])
    store.add(scope)

    with pytest.raises(ScopeError, match="already exists"):
        store.add(scope)
    with pytest.raises(KeyError):
        store.get("missing")


def test_widening_is_not_possible_from_a_stored_scope(tmp_path):
    """A stored scope cannot gain actions: the object is frozen and the store has no edit path."""
    store = ScopeStore(tmp_path)
    scope = issue_scope("owner", "cli", "sandbox/notes", ["write_note"], ttl_seconds=None)
    store.add(scope)

    loaded = store.get(scope.scope_id)
    with pytest.raises(AttributeError):
        loaded.allowed_actions.add("send_mail")  # type: ignore[attr-defined]
    assert ScopeStore(tmp_path).get(scope.scope_id).allowed_actions == {"write_note"}
