"""The local draft path: draft_create_local writes a record and there is no send anywhere."""

import json

import pytest

from loom.core.config import Settings
from loom.tools import make_tools
from loom.tools.drafts import DraftStore


def _registry(tmp_path):
    settings = Settings(loom_env="dev", loom_data_dir=tmp_path, loom_sandbox_root=tmp_path / "sandbox")
    return make_tools(settings, {"browser": _NoBrowser(), "calendar": None, "mail": None}), settings


class _NoBrowser:
    """The registry builds the real browser transport lazily; a stand-in keeps tests offline."""

    def pending_approval(self):
        return {}


def test_draft_create_local_writes_a_local_only_record(tmp_path):
    registry, _ = _registry(tmp_path)

    outcome = registry["draft_create_local"].func.invoke(
        {"to": "landlord@example.com", "subject": "Heating bill", "body": "We agree with the split."}
    )

    assert "has not been sent and cannot be sent" in outcome
    drafts = DraftStore(tmp_path).list()
    assert len(drafts) == 1
    assert drafts[0].to == "landlord@example.com"
    assert drafts[0].status == "local_only"
    assert registry["draft_create_local"].side_effect is True


def test_draft_create_local_refuses_oversized_content(tmp_path):
    registry, _ = _registry(tmp_path)

    with pytest.raises(ValueError, match="character limit"):
        registry["draft_create_local"].func.invoke({"to": "a@b.c", "subject": "s", "body": "x" * 10_001})
    assert DraftStore(tmp_path).list() == []


def test_draft_create_local_carries_and_validates_the_category(tmp_path):
    registry, _ = _registry(tmp_path)

    registry["draft_create_local"].func.invoke(
        {"to": "landlord@example.com", "subject": "Heating bill", "body": "We agree.", "category": "reply"}
    )

    drafts = DraftStore(tmp_path).list()
    assert len(drafts) == 1 and drafts[0].category == "reply"

    with pytest.raises(ValueError, match="category must be one of"):
        registry["draft_create_local"].func.invoke(
            {"to": "a@b.c", "subject": "s", "body": "b", "category": "send-it"}
        )
    assert len(DraftStore(tmp_path).list()) == 1  # the refused category wrote nothing


def test_old_draft_records_load_with_an_empty_category(tmp_path):
    store = DraftStore(tmp_path)
    store.add(to="a@b.c", subject="s", body="b")
    path = tmp_path / "drafts.json"
    record = json.loads(path.read_text(encoding="utf-8"))[0]
    del record["category"]
    path.write_text(json.dumps([record]), encoding="utf-8")

    loaded = DraftStore(tmp_path).list()

    assert len(loaded) == 1 and loaded[0].category == ""


def test_no_send_symbol_exists_anywhere_in_the_registry_or_drafts(tmp_path):
    registry, _ = _registry(tmp_path)
    source = ""
    for path in (tmp_path.parent.parent / "src" / "loom" / "tools").rglob("*.py"):
        source += path.read_text(encoding="utf-8")

    assert not any("send" in name for name in registry)
    assert "def draft_send" not in source and "smtp" not in source.lower()
