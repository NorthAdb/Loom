"""CLI memory tests through main(argv)."""

import loom.cli


class FakeSemantic:
    """Semantic memory double with a dict store; records the queries it served."""

    def __init__(self, store=None, error=None):
        self.store = store if store is not None else {}
        self.error = error
        self.searched = []

    def add(self, texts):
        for text in texts:
            self.store[text] = text

    def search(self, query, limit=3):
        self.searched.append(query)
        if self.error:
            raise self.error
        return list(self.store.values())[:limit]


def run(capsys, argv):
    code = loom.cli.main(argv)
    return code, capsys.readouterr().out


def test_facts_set_list_del_round_trip(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path))

    code, out = run(capsys, ["memory", "facts", "set", "reply_language", "中文"])
    assert code == 0
    assert out.startswith("- reply_language: 中文 (human, ")

    code, out = run(capsys, ["memory", "facts"])
    assert code == 0
    assert "- reply_language: 中文" in out

    code, out = run(capsys, ["memory", "facts", "del", "reply_language"])
    assert code == 0
    assert out.strip() == "removed fact reply_language"

    code, out = run(capsys, ["memory", "facts"])
    assert code == 0
    assert out.strip() == "(no facts)"


def test_bare_memory_command_lists_facts(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path))
    assert loom.cli.main(["memory", "facts", "set", "tone", "concise"]) == 0

    code, out = run(capsys, ["memory"])

    assert code == 0
    assert "- tone: concise" in out


def test_facts_del_missing_key_fails_with_reason(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path))

    code, out = run(capsys, ["memory", "facts", "del", "absent"])

    assert code == 1
    assert "error:" in out
    assert "absent" in out


def test_facts_set_rejects_secret_value_and_sensitive_key(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path))

    code, out = run(capsys, ["memory", "facts", "set", "note", "key", "sk-abc123def456ghijkl"])
    assert code == 1
    assert "error: refusing to store fact" in out

    code, out = run(capsys, ["memory", "facts", "set", "api_key", "anything"])
    assert code == 1
    assert "error: refusing to store fact" in out


def test_search_prints_hits_from_the_memory_factory(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path))
    fake = FakeSemantic(store={"likes concise answers": "likes concise answers"})
    monkeypatch.setattr(loom.cli, "build_semantic_memory", lambda settings: fake)

    code, out = run(capsys, ["memory", "search", "reply", "style"])

    assert code == 0
    assert "- likes concise answers" in out
    assert fake.searched == ["reply style"]


def test_search_empty_store_prints_placeholder(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(loom.cli, "build_semantic_memory", lambda settings: FakeSemantic())

    code, out = run(capsys, ["memory", "search", "anything"])

    assert code == 0
    assert out.strip() == "(no memories)"


def test_search_reports_config_error_without_a_key(monkeypatch, tmp_path, capsys):
    """Memory enabled plus no key: the real factory raises ConfigError naming the variable."""
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOOM_MEMORY_ENABLED", "true")

    code, out = run(capsys, ["memory", "search", "anything"])

    assert code == 1
    assert "LLM_API_KEY" in out


def test_search_reports_runtime_error_as_one_line(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LOOM_ENV", "dev")
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path))
    broken = FakeSemantic(error=RuntimeError("endpoint unreachable"))
    monkeypatch.setattr(loom.cli, "build_semantic_memory", lambda settings: broken)

    code, out = run(capsys, ["memory", "search", "anything"])

    assert code == 1
    assert out.strip() == "error: endpoint unreachable"


def test_main_catches_config_error_from_bad_env(monkeypatch, tmp_path, capsys):
    """A missing LOOM_ENV through main prints a config error instead of a traceback."""
    monkeypatch.delenv("LOOM_ENV", raising=False)
    monkeypatch.setenv("LOOM_DATA_DIR", str(tmp_path))

    code, out = run(capsys, ["memory", "facts"])

    assert code == 1
    assert "config error:" in out
    assert "LOOM_ENV" in out
