"""Unit tests for the eval harness: parsing, judging, the canary rule, and the rate."""

import json
from pathlib import Path

import pytest

from loom.eval import EvalCase, format_summary, load_cases, run_case, run_cases


def write_case(directory: Path, name: str, raw: dict) -> Path:
    path = directory / f"{name}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def green_case_raw() -> dict:
    return {
        "name": "always_green",
        "user": "Say hello.",
        "steps": [],
        "final": "hello",
        "expect": {"final_contains": "hello"},
    }


def test_load_cases_parses_a_valid_case(tmp_path):
    write_case(tmp_path, "parses_ok", green_case_raw() | {"name": "parses_ok"})

    cases = load_cases(tmp_path)

    assert [case.name for case in cases] == ["parses_ok"]
    assert cases[0].user == "Say hello."
    assert cases[0].expect == {"final_contains": "hello"}
    assert cases[0].gate is None and cases[0].expect_fail is False


def test_load_cases_names_the_file_on_bad_json(tmp_path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match=r"broken\.json.*not valid JSON"):
        load_cases(tmp_path)


def test_load_cases_rejects_unknown_fields_bad_gates_and_wrong_names(tmp_path):
    for name, patch, pattern in (
        ("unknown_field", {"surprise": 1}, r"unknown_field\.json.*surprise"),
        ("bad_gate", {"gate": "yes"}, r"bad_gate\.json.*gate"),
    ):
        directory = tmp_path / name
        directory.mkdir()
        write_case(directory, name, green_case_raw() | {"name": name} | patch)
        with pytest.raises(ValueError, match=pattern):
            load_cases(directory)

    directory = tmp_path / "misnamed"
    directory.mkdir()
    write_case(directory, "misnamed", green_case_raw() | {"name": "other_name"})
    with pytest.raises(ValueError, match=r"misnamed\.json.*other_name"):
        load_cases(directory)


def test_run_case_judges_an_unplanned_interrupt_red(tmp_path):
    canary = EvalCase(
        name="canary",
        user="write it",
        steps=[{"tool": "write_file", "args": {"path": "a.txt", "content": "x"}}],
        final="wrote",
        expect={"interrupted": False},
        expect_fail=True,
    )

    result = run_case(canary, tmp_path)

    assert result.passed is False
    assert "no gate" in result.detail
    assert "write_file" in result.detail


def test_run_case_turns_a_thrown_error_into_a_failed_result(tmp_path):
    broken = EvalCase(name="broken", user="hi", seed_facts={"password": "hunter2"})

    result = run_case(broken, tmp_path)

    assert result.passed is False
    assert "password" in result.detail


def test_run_case_checks_the_budget_and_audit_expectations(tmp_path):
    braked = EvalCase(name="braked", user="hello", budget_tokens=1, expect={"refused": True, "audit_budget_rejected": True})
    unlimited = EvalCase(name="unlimited", user="Say hello.", final="hello", expect={"refused": False, "spend_recorded": False})

    assert run_case(braked, tmp_path).passed is True
    assert run_case(unlimited, tmp_path).passed is True


def test_run_cases_rate_excludes_the_canary(tmp_path):
    write_case(tmp_path, "a_green", green_case_raw() | {"name": "a_green"})
    write_case(tmp_path, "b_broken", green_case_raw() | {"name": "b_broken", "final": "goodbye"})
    write_case(tmp_path, "c_canary", green_case_raw() | {"name": "c_canary", "final": "goodbye", "expect_fail": True})

    summary = run_cases(tmp_path)

    assert summary.rate == pytest.approx(0.5)
    assert summary.canary_ok is True


def test_a_green_canary_fails_the_summary(tmp_path):
    write_case(tmp_path, "canary", green_case_raw() | {"name": "canary", "expect_fail": True})

    summary = run_cases(tmp_path)

    assert summary.canary_ok is False
    assert "[FAIL] canary" in format_summary(summary)
