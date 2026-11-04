"""Regression eval harness behind `loom eval`.

Declarative cases (JSON files) run through the real graph with a scripted model
and fake transports; expectations judge the transcript, the approval gate, the
budget brake, and the audit. The harness's runtime fixtures (the scripted model
and the fake transports) live here, not in tests/, because the runner must
never import from tests/ or conftest.
"""

import json
import shutil
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from loom.audit.log import AuditLog
from loom.browser import (
    _PASSWORD_MASK,
    BrowserField,
    PageState,
    SubmitOutcome,
    _ensure_allowed,
    _parse_allowed_hosts,
)
from loom.core.config import Settings
from loom.core.graph import build_graph
from loom.memory import FactsStore
from loom.ops.budget import BudgetGuard, SpendLedger, turn_tokens

BUDGET_REJECTED_REASON = "daily budget exceeded"

_EXPECT_TYPES = {
    "interrupted": bool,
    "refused": bool,
    "final_contains": str,
    "memory_note_contains": str,
    "submitted_count": int,
    "mail_appended": int,
    "audit_budget_rejected": bool,
    "spend_recorded": bool,
}


@dataclass(frozen=True)
class EvalCase:
    """One declarative eval case parsed from tests/eval/cases/<name>.json.

    `steps` are the scripted model's tool calls, in order; `final` is the
    scripted closing text the model answers with after the last step. `gate`
    is the auto-decision when the graph interrupts; None means the case must
    not interrupt at all. `ics` may contain a "{today}" token, replaced with
    the run date in the settings timezone (a static file cannot name today).
    """

    name: str
    user: str
    steps: list[dict] = field(default_factory=list)
    final: str = ""
    gate: str | None = None
    budget_tokens: int = 0
    seed_facts: dict[str, str] = field(default_factory=dict)
    browser_pages: dict[str, dict] = field(default_factory=dict)
    browser_allowed_hosts: str = ""
    ics: str | None = None
    mail: bool = False
    expect: dict[str, Any] = field(default_factory=dict)
    expect_fail: bool = False

    @classmethod
    def from_dict(cls, raw, source) -> "EvalCase":
        """Parse one case dict; `source` (a file path) names the file in every error."""
        def need(condition, message):
            if not condition:
                raise ValueError(f"{source}: {message}")

        need(isinstance(raw, dict), "a case must be a JSON object")
        unknown = sorted(set(raw) - _FIELDS)
        need(not unknown, f"unknown field(s): {', '.join(unknown)}")
        need(isinstance(raw.get("name"), str) and raw["name"].strip(), "'name' must be a non-empty string")
        need(isinstance(raw.get("user"), str) and raw["user"], "'user' must be a non-empty string")
        steps = raw.get("steps", [])
        need(
            isinstance(steps, list) and all(_is_step(step) for step in steps),
            "'steps' must be a list of {\"tool\": str, \"args\": dict} objects",
        )
        need(isinstance(raw.get("final", ""), str), "'final' must be a string")
        gate = raw.get("gate")
        need(gate is None or gate in ("approve", "reject"), "'gate' must be \"approve\" or \"reject\"")
        budget = raw.get("budget_tokens", 0)
        need(
            isinstance(budget, int) and not isinstance(budget, bool) and budget >= 0,
            "'budget_tokens' must be a non-negative integer",
        )
        seed = raw.get("seed_facts", {})
        need(
            isinstance(seed, dict) and all(isinstance(key, str) and isinstance(value, str) for key, value in seed.items()),
            "'seed_facts' must map string keys to string values",
        )
        pages = raw.get("browser_pages", {})
        need(
            isinstance(pages, dict) and all(_is_page(spec) for spec in pages.values()),
            "'browser_pages' must map a url to {\"title\": str, \"fields\": [{\"name\", \"label\", \"kind\", \"value\"}]}",
        )
        need(isinstance(raw.get("browser_allowed_hosts", ""), str), "'browser_allowed_hosts' must be a string")
        ics = raw.get("ics")
        need(ics is None or isinstance(ics, str), "'ics' must be a string")
        need(isinstance(raw.get("mail", False), bool), "'mail' must be a boolean")
        expect = raw.get("expect", {})
        need(isinstance(expect, dict), "'expect' must be an object")
        for key, value in expect.items():
            need(key in _EXPECT_TYPES, f"unknown expectation {key!r} (known: {', '.join(sorted(_EXPECT_TYPES))})")
            need(_matches(value, _EXPECT_TYPES[key]), f"expectation {key!r} must be {_EXPECT_TYPES[key].__name__}")
        need(isinstance(raw.get("expect_fail", False), bool), "'expect_fail' must be a boolean")
        return cls(
            name=raw["name"],
            user=raw["user"],
            steps=steps,
            final=raw.get("final", ""),
            gate=gate,
            budget_tokens=budget,
            seed_facts=seed,
            browser_pages=pages,
            browser_allowed_hosts=raw.get("browser_allowed_hosts", ""),
            ics=ics,
            mail=raw.get("mail", False),
            expect=expect,
            expect_fail=raw.get("expect_fail", False),
        )


_FIELDS = frozenset(EvalCase.__dataclass_fields__)


@dataclass(frozen=True)
class CaseResult:
    name: str
    passed: bool
    detail: str
    expect_fail: bool

    @property
    def met_expectation(self) -> bool:
        """A normal case meets its expectation by passing; a canary, by judging red."""
        return self.passed != self.expect_fail


@dataclass(frozen=True)
class EvalSummary:
    """Per-case results plus the rate over normal cases and the canary verdict.

    `rate` is passed_normal / total_normal; expect_fail cases are excluded from
    both. `canary_ok` means every expect_fail case judged red — a canary whose
    expectations unexpectedly held fails the whole summary.
    """

    results: list[CaseResult]
    rate: float
    canary_ok: bool


class ScriptedChatModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class FakeMailTransport:
    """Mail transport double: records drafts instead of touching IMAP."""

    def __init__(self):
        self.appended = []
        self.replaced = []

    def append_draft(self, message_bytes):
        self.appended.append(message_bytes)
        return f"fake-draft-{len(self.appended)}"

    def replace_draft(self, old_id, message_bytes):
        self.replaced.append((old_id, message_bytes))
        return f"fake-draft-{len(self.appended) + 1}"


class FakeCalendarTransport:
    """Calendar transport double: serves a fixed ICS body."""

    def __init__(self, ics: bytes):
        self.ics = ics

    def fetch_ics(self) -> bytes:
        return self.ics


def _masked(fields: tuple[BrowserField, ...]) -> tuple[BrowserField, ...]:
    return tuple(
        replace(f, value=_PASSWORD_MASK) if f.kind == "password" and f.value else f for f in fields
    )


class FakeBrowserTransport:
    """Browser transport double: prebuilt pages, fills recorded into field values.

    `pages` maps a url to a page spec. It enforces the same host allowlist as
    the real transport (before any page lookup) and masks password fields the
    same way on open, fill, and pending_approval, so a case like
    browser_open_refuses_offsite_host never needs playwright and gate payloads
    built on this fake can never carry a plaintext password either.
    """

    def __init__(self, pages: dict | None = None, allowed_hosts: str = ""):
        self.pages = pages or {}
        self.allowed = _parse_allowed_hosts(allowed_hosts)
        self.current: PageState | None = None
        self.submitted: list[SubmitOutcome] = []

    def open(self, url):
        _ensure_allowed(url, self.allowed)
        page = self.pages.get(url)
        if page is None:
            raise RuntimeError(f"no fixture page for {url}")
        self.current = (
            PageState(url=url, title=page, fields=()) if isinstance(page, str) else PageState(url=page.url, title=page.title, fields=_masked(page.fields))
        )
        return self.current

    def fill(self, fields):
        if self.current is None:
            raise RuntimeError("no page is open; call browser_open first")
        by_name = {f.name: f for f in self.current.fields}
        unknown = [name for name in fields if name not in by_name]
        if unknown:
            raise ValueError(
                f"no field(s) named {', '.join(map(repr, unknown))}; "
                f"the page has: {', '.join(by_name) or '(none)'}"
            )
        self.current = replace(
            self.current,
            fields=_masked(
                tuple(
                    BrowserField(f.name, f.label, f.kind, str(fields.get(f.name, f.value)))
                    for f in self.current.fields
                )
            ),
        )
        return self.current

    def submit(self):
        if self.current is None:
            raise RuntimeError("no page is open; call browser_open first")
        outcome = SubmitOutcome(url=self.current.url, accepted=True, detail="Submission received")
        self.submitted.append(outcome)
        return outcome

    def pending_approval(self) -> dict:
        if self.current is None:
            return {"state": "no page is open"}
        return {"url": self.current.url, "fields": {f.name: f.value for f in _masked(self.current.fields)}}


def _is_step(step) -> bool:
    return isinstance(step, dict) and isinstance(step.get("tool"), str) and isinstance(step.get("args"), dict)


def _is_page(spec) -> bool:
    return (
        isinstance(spec, dict)
        and isinstance(spec.get("title"), str)
        and isinstance(spec.get("fields"), list)
        and all(
            isinstance(f, dict) and all(isinstance(f.get(key), str) for key in ("name", "label", "kind", "value"))
            for f in spec["fields"]
        )
    )


def _matches(value, kind) -> bool:
    if kind is bool:
        return isinstance(value, bool)
    if kind is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, str)


def load_cases(directory) -> list[EvalCase]:
    """Parse every *.json under the directory in name order; a bad file is a ValueError naming it."""
    cases = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"{path}: not valid JSON ({exc})") from exc
        case = EvalCase.from_dict(raw, path)
        if case.name != path.stem:
            raise ValueError(f"{path}: case name {case.name!r} must match the file name {path.stem!r}")
        cases.append(case)
    return cases


def tool_call_msg(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    """Build an AIMessage carrying one tool call, as a model would emit."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def _transports(case: EvalCase, tz: ZoneInfo) -> dict:
    """The fake transports a case declared; sandbox tools stay on the real jailed dispatcher."""
    pages = {}
    for url, spec in case.browser_pages.items():
        fields = tuple(BrowserField(f["name"], f["label"], f["kind"], f["value"]) for f in spec["fields"])
        pages[url] = PageState(url=url, title=spec["title"], fields=fields)
    transports: dict = {"browser": FakeBrowserTransport(pages, case.browser_allowed_hosts)}
    if case.ics is not None:
        body = case.ics.replace("{today}", datetime.now(tz).strftime("%Y%m%d"))
        transports["calendar"] = FakeCalendarTransport(body.encode("utf-8"))
    if case.mail:
        transports["mail"] = FakeMailTransport()
    return transports


def _settings(case: EvalCase, data_dir: Path, sandbox_root: Path) -> Settings:
    # Every env-backed field is explicit so a host .env or environment cannot
    # leak into a case run (Settings reads both; pytest's clean_env fixture
    # does not cover `loom eval` runs).
    return Settings(
        loom_env="dev",
        loom_log_level="info",
        loom_data_dir=data_dir,
        loom_sandbox_root=sandbox_root,
        loom_sandbox_url="",
        llm_api_key="",
        llm_model="eval-model",
        llm_base_url="",
        memory_enabled=False,
        memory_inject_limit=5,
        ical_url="",
        imap_host="",
        imap_port=993,
        mail_user="owner@example.com",
        mail_password="",
        mail_folder="Drafts",
        timezone_name="Asia/Hong_Kong",
        browser_allowed_hosts=case.browser_allowed_hosts,
        daily_budget_tokens=0,
        token_price_usd_per_1k=0.0,
    )


def _budget_guard(case: EvalCase, data_dir: Path) -> BudgetGuard | None:
    if case.budget_tokens <= 0:
        return None
    ledger = SpendLedger(data_dir)
    # The brake refuses a turn only once today's spend has reached the budget,
    # so a budget case starts with the day already spent up to its line.
    ledger.record("chat", f"{case.name}:prior", case.budget_tokens)
    return BudgetGuard(case.budget_tokens, ledger, AuditLog(data_dir))


def _jsonl_entries(directory: Path):
    for path in sorted(Path(directory).glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                yield entry


def _has_budget_rejection(data_dir: Path) -> bool:
    for record in _jsonl_entries(Path(data_dir) / "audit"):
        if record.get("decision") != "rejected":
            continue
        try:
            args = json.loads(record.get("args", ""))
        except ValueError:
            continue
        if isinstance(args, dict) and args.get("reason") == BUDGET_REJECTED_REASON:
            return True
    return False


def _has_spend_for(data_dir: Path, run_id: str) -> bool:
    return any(entry.get("run_id") == run_id for entry in _jsonl_entries(Path(data_dir) / "spend"))


_CONTAINS_KEYS = frozenset({"final_contains", "memory_note_contains"})


def _judge(case: EvalCase, *, interrupted, unplanned_tools, refused, result, transports, data_dir: Path) -> CaseResult:
    if unplanned_tools:
        return CaseResult(case.name, False, f"interrupted but the case declares no gate ({unplanned_tools})", case.expect_fail)
    checks = {
        "interrupted": interrupted,
        "refused": refused,
        "final_contains": str(result["messages"][-1].content) if result else "",
        "memory_note_contains": (result or {}).get("memory_note", ""),
        "submitted_count": len(transports["browser"].submitted),
        "mail_appended": len(getattr(transports.get("mail"), "appended", ())),
        "audit_budget_rejected": _has_budget_rejection(data_dir),
        "spend_recorded": _has_spend_for(data_dir, case.name),
    }
    for key, expected in case.expect.items():
        actual = checks[key]
        held = expected in actual if key in _CONTAINS_KEYS else actual == expected
        if not held:
            return CaseResult(case.name, False, f"expected {key}={expected!r}, got {actual!r}", case.expect_fail)
    return CaseResult(case.name, True, "", case.expect_fail)


def _run_case(case: EvalCase, data_dir: Path, sandbox_root: Path) -> CaseResult:
    graph = None
    try:
        sandbox_root.mkdir(parents=True, exist_ok=True)
        for key, value in case.seed_facts.items():
            FactsStore(data_dir).upsert(key, value)
        settings = _settings(case, data_dir, sandbox_root)
        transports = _transports(case, ZoneInfo(settings.timezone_name))
        script = [tool_call_msg(step["tool"], step["args"]) for step in case.steps]
        script.append(AIMessage(content=case.final))
        graph = build_graph(ScriptedChatModel(messages=iter(script)), settings, transports=transports)

        guard = _budget_guard(case, data_dir)
        interrupted = False
        unplanned_tools = ""
        refused = False
        result = None
        if guard is not None and not guard.allow("chat", case.name):
            refused = True
        else:
            config = {"configurable": {"thread_id": case.name}}
            result = graph.invoke({"messages": [HumanMessage(content=case.user)], "run_id": case.name}, config)
            while "__interrupt__" in result:
                if case.gate is None:
                    unplanned_tools = ", ".join(item["name"] for item in result["__interrupt__"][0].value["tools"])
                    break
                interrupted = True
                result = graph.invoke(Command(resume={"decision": case.gate}), config)
            if guard is not None:
                guard.record("chat", case.name, turn_tokens(result["messages"]))
        return _judge(
            case,
            interrupted=interrupted,
            unplanned_tools=unplanned_tools,
            refused=refused,
            result=result,
            transports=transports,
            data_dir=data_dir,
        )
    finally:
        # The checkpointer's sqlite connection must close before the temp dir
        # goes: an open handle makes rmtree fail on Windows.
        saver = getattr(graph, "checkpointer", None)
        conn = getattr(saver, "conn", None)
        if conn is not None:
            with suppress(Exception):
                conn.close()


def run_case(case: EvalCase, cases_dir_parent=None) -> CaseResult:
    """Run one case in a fresh temp data dir (cleaned up); a thrown error is a failed result, not a crashed run."""
    root = Path(tempfile.mkdtemp(prefix="loom-eval-"))
    try:
        return _run_case(case, root / "data", root / "sandbox")
    except Exception as exc:  # noqa: BLE001 - one broken case is a red result
        where = f"{Path(cases_dir_parent) / f'{case.name}.json'}: " if cases_dir_parent else ""
        return CaseResult(case.name, False, f"{where}{exc}", case.expect_fail)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run_cases(directory) -> EvalSummary:
    """Load every case under the directory, run them in name order, and summarize."""
    results = [run_case(case, Path(directory)) for case in load_cases(directory)]
    normals = [result for result in results if not result.expect_fail]
    rate = sum(result.passed for result in normals) / len(normals) if normals else 0.0
    canary_ok = all(not result.passed for result in results if result.expect_fail)
    return EvalSummary(results=results, rate=rate, canary_ok=canary_ok)


def format_result(result: CaseResult) -> str:
    if result.expect_fail:
        if not result.passed:
            return f"[RED] {result.name} — expected failure, judged red"
        return f"[FAIL] {result.name} — expected failure, but every expectation held"
    if result.passed:
        return f"[PASS] {result.name}"
    return f"[FAIL] {result.name} — {result.detail}"


def format_summary(summary: EvalSummary) -> str:
    """One line per case, then the rate line and the canary line; the caller owns exit codes."""
    lines = [format_result(result) for result in summary.results]
    normals = [result for result in summary.results if not result.expect_fail]
    percent = summary.rate * 100
    shown = f"{percent:.0f}" if percent == int(percent) else f"{percent:.1f}"
    lines.append(f"eval: {sum(result.passed for result in normals)}/{len(normals)} passed ({shown}%)")
    if not any(result.expect_fail for result in summary.results):
        lines.append("canary: no expect_fail case in this run")
    elif summary.canary_ok:
        lines.append("canary: expect_fail judged red")
    else:
        lines.append("canary: expect_fail judged GREEN — run failed")
    return "\n".join(lines)
