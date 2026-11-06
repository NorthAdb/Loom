# Resume story

What this repository demonstrates, for a reader who will only skim. Every number in the table below is reproduced by a command run at the repository root; nothing here claims more than the tree shows.

## The problem

A personal AI agent that acts — writing files, drafting mail, filling web forms — is one unreviewed step away from doing damage. The hard part is not making the model call tools; it is making every consequential step stop and wait for a person, every day, on one machine, without becoming too annoying to use. Loom is that loop: a self-hosted single-user agent where the model proposes, a Request Scope bounds the run, the Tool Gateway checks every call outside the model and fails closed, a human decides on the real payload, and the evidence lands in an append-only audit log under one `correlation_id`.

## What is running

The core is a LangGraph graph (`src/loom/core/graph.py`): memory note → agent → gate for side-effect tools → tools, with sqlite checkpoints per thread id. Seventeen registered tools carry a declared side-effect flag; the gate interrupts with the tool's arguments before anything executes, and mail drafts additionally carry their full text in the approval payload. Browser fill goes through a request firewall that aborts every non-allowlisted request before it is sent, and the submit approval shows the page's real state read back from the browser, not the model's claims ([ADR-011](DECISIONS.md)). Inbound content is data, never instructions: a scoped run issues the minimal Request Scope (`send_permission` always false, `memory_write` default false, `issue_scope` the only mint), the Tool Gateway checks each call at the gate and again at execution, and a scope whose `memory_write` is false refuses every memory-writing action ([ADR-014](DECISIONS.md)). The Gate 8 triage path is running behind the same seam: a read-only IMAP fetch feeds the inbound seam, `run_triage` drives the scoped turn (a message with no scope skips the run entirely), and the default write is one categorized local draft; the mailbox IMAP draft stays a separate scope plus approval through the layer-4 control plane. MCP servers sit under the same gates ([ADR-020](DECISIONS.md)): registration stores a schema fingerprint and an allowed tool subset, a drifted fingerprint or a forced re-registration freezes the server until `loom mcp review` re-probes it, every call is an `mcp:<server_id>:<tool>` action through the Tool Gateway with the fingerprint re-verified at call time, credentials are env-var names whose values are never stored, and tool return values are untrusted data. Email has no send path in the codebase at all — drafts are IMAP APPENDs with the `\Draft` flag or `local_only` records in `drafts.json`, and a guard test greps for send verbs. The audit log is append-only JSONL; every Gate 7 row threads the scope's `correlation_id`. The design decisions and their consequences live in [DECISIONS.md](DECISIONS.md), the security boundary in [SECURITY.md](SECURITY.md), and the implemented-state source of truth in [STATUS.md](STATUS.md).

## Numbers you can check

| Claim | Number | Reproduce with |
| --- | --- | --- |
| Test suite (prior local self-report) | 543 passed, 1 skipped with chromium installed, plus one pre-existing environment-dependent failure (the redirect-landing browser test needs direct egress to example.com; it fails identically on the pre-rework tree in a proxied sandbox). Historical figure from the pre-merge browser-equipped environment — not re-borrowed as a fresh full-suite claim on this docs tip | `pytest -q` (local, chromium) |
| Test suite (CI as of main `6b88f20`) | 535 passed, 12 skipped on head CI `test` (Python 3.11/3.12); `audit` and GitGuardian green. Real-browser tests skip on CI runners | GitHub Actions on main tip `6b88f20` |
| Round-2 regressions | 9 tests in `tests/test_round2_rework.py` (was 7 before the `f892bdd` long-secret scrub-before-cap follow-up) | `pytest -q tests/test_round2_rework.py` |
| Capabilities behind the gate | 17 registered tools, side-effect flags pinned exactly | `pytest tests/test_gate3_guards.py tests/test_gate4_guards.py -q` |
| Scope and gateway | every scoped call checked at the gate and at execution; fail closed, refusal audited, `memory_write` enforced | `pytest tests/test_tool_gateway.py tests/test_runtime_gateway.py -q` |
| MCP lifecycle | fingerprint freeze, out-of-subset refusal, untrusted return, expired scope, audit fail-closed — the §4.7 set through the real graph | `pytest -k 'mcp or fingerprint or gate9' -q` |
| Regression eval | 18 cases, 17 counted + 1 expect_fail canary judged red, 100% counted pass rate | `loom eval` |
| Demos | 11 demo scripts, 41 scenarios, all passing (the browser one also needs chromium) | run each `scripts/demo_*.py` |
| Audit trail | one JSON line per decision: `ts`, `run_id`, `actor`, `tool`, `args`, `decision`, plus `correlation_id` on every Gate 7 row | `pytest tests/test_audit.py tests/test_audit_correlation.py -q` |
| Daily budget brake | refused turn + audit row `reason=daily budget exceeded` at a 1-token limit | `pytest tests/test_budget.py -q` |
| Recoverability | 9 data-dir roots in a zip; restore refuses archive members that escape the data dir; `scopes.json` deliberately excluded (ADR-019) | `pytest tests/test_backup.py -q` |

## Tradeoffs

- **Fill ungated, submit gated.** Typing into a form runs without approval because it submits nothing; the single click that submits is the only gated browser verb. The request firewall closes the gap a script could otherwise drive through.
- **The budget is tokens, not dollars.** Dollars require inventing a per-model price; tokens come from the API's own usage metadata and brake exactly. An optional price key feeds a dollars estimate into `loom stats` only.
- **Snapshots deferred.** The browser gate shows a url + field dictionary instead of a screenshot; screenshots are a later layer ([ADR-011](DECISIONS.md)).
- **Two memory stores with one priority rule.** The self-owned facts table is authoritative; Mem0 recall is reference. Conflicts resolve toward the table by injection order, and secrets are refused at both write paths. The light Context OS (episodic, digest, feedback, compact) and the task state machine are in the tree ([ADR-013](DECISIONS.md), Gate 6).
- **Inbound runs are memory-blind.** A scoped run carries untrusted content, so it writes no facts, no semantic memory, and no episodic rows; the registry's `memory_write` classification and the scope's `memory_write=false` are also enforced at the Tool Gateway, not just skipped in the graph.
- **Approval resumes are not re-budgeted.** A resumed run belongs to a turn a person already admitted, and each resume still needs that person.

## Try it

```bash
pip install -e ".[dev,browser]" && playwright install chromium
cp .env.example .env            # set LOOM_ENV=dev; LLM_API_KEY only for real chat
loom doctor
python scripts/demo_gate7_inbound.py   # the Life Operator loop: inbound, scope, gateway, local draft
python scripts/demo_gate8_mail_triage.py   # real-shaped mail: fetch, scoped triage, local draft, layer 4
python scripts/demo_gate9_mcp.py       # MCP under the same gates: fingerprint, freeze, review, untrusted returns
python scripts/demo_gate5.py           # budget brake, backup/restore, eval summary
python scripts/demo_gate4_browser.py
loom eval
```

The demos are keyless (scripted models, local fixtures); the full README walk is in [the README](../README.md).

## In progress and not yet claimed

Gate 8's feature code (real mail triage, default write is a local draft) is on main tip `6b88f20` (PR #24); it is not Passed — the ≥2-week owner dogfood on real mail is still open, and console Fetch against a real mailbox was never the acceptance evidence, so its three checklist rows stay unchecked ([STATUS.md](STATUS.md)). Gate 9's feature code (the MCP registry with schema fingerprints, credentials as env-var handles, and untrusted returns) is also on that main tip; original final acceptance stays not Passed and its three checklist rows stay unchecked — round-2 four items + the long-secret follow-up are closed in code, and latest CI on `6b88f20` is green, but a limited-scope local review is not full original acceptance and is not an all-green claim. Plan and decisions: [ROADMAP.md](ROADMAP.md), [DECISIONS.md](DECISIONS.md). This page does not claim Gate 8 or Gate 9 as Passed.
