# Resume story

What this repository demonstrates, for a reader who will only skim. Every number in the table below is reproduced by a command run at the repository root; nothing here claims more than the tree shows.

## The problem

A personal AI agent that acts — writing files, drafting mail, filling web forms — is one unreviewed step away from doing damage. The hard part is not making the model call tools; it is making every consequential step stop and wait for a person, every day, on one machine, without becoming too annoying to use. Loom is that loop: a self-hosted single-user agent where the model proposes, a gate holds the side effects, a human decides, and the evidence lands in an append-only audit log.

## What is running

The core is a LangGraph graph (`src/loom/graph.py`): memory note → agent → gate for side-effect tools → tools, with sqlite checkpoints per thread id. Thirteen capabilities are registered tools with a declared side-effect flag (16 entries including the browser trio); the gate interrupts with the tool's arguments before anything executes, and mail drafts additionally carry their full text in the approval payload. Browser fill goes through a request firewall that aborts every non-allowlisted request before it is sent, and the submit approval shows the page's real state read back from the browser, not the model's claims ([ADR-011](DECISIONS.md)). Email has no send path in the codebase at all — drafts are IMAP APPENDs with the `\Draft` flag, and a guard test greps for send verbs. The design decisions and their consequences live in [DECISIONS.md](DECISIONS.md), the security boundary in [SECURITY.md](SECURITY.md), and the implemented-state source of truth in [STATUS.md](STATUS.md).

## Numbers you can check

| Claim | Number | Reproduce with |
| --- | --- | --- |
| Test suite | 320 passed, 1 skipped | `pytest -q` |
| Capabilities behind the gate | 16 registered tools, side-effect flags pinned exactly | `pytest tests/test_gate3_guards.py tests/test_gate4_guards.py -q` |
| Regression eval | 12 cases, 11 counted + 1 expect_fail canary judged red, 100% counted pass rate | `loom eval` |
| Demos, all keyless | 6 demo scripts, 19 scenarios, all passing | `python scripts/demo_gate2.py` and the five siblings named in the README |
| Audit trail | one JSON line per decision with 6 fields (`ts`, `run_id`, `actor`, `tool`, `args`, `decision`) | `pytest tests/test_audit.py -q` |
| Daily budget brake | refused turn + audit row `reason=daily budget exceeded` at a 1-token limit | `pytest tests/test_budget.py -q` |
| Recoverability | 5 data-dir roots in a zip; restore refuses archive members that escape the data dir | `pytest tests/test_backup.py -q` |

## Tradeoffs

- **Fill ungated, submit gated.** Typing into a form runs without approval because it submits nothing; the single click that submits is the only gated browser verb. The request firewall closes the gap a script could otherwise drive through.
- **The budget is tokens, not dollars.** Dollars require inventing a per-model price; tokens come from the API's own usage metadata and brake exactly. An optional price key feeds a dollars estimate into `loom stats` only.
- **Snapshots deferred.** The browser gate shows a url + field dictionary instead of a screenshot; screenshots are a later layer ([ADR-011](DECISIONS.md)).
- **Two memory stores with one priority rule.** The self-owned facts table is authoritative; Mem0 recall is reference. Conflicts resolve toward the table by injection order, and secrets are refused at both write paths.
- **Approval resumes are not re-budgeted.** A resumed run belongs to a turn a person already admitted, and each resume still needs that person.

## Try it

```bash
pip install -e ".[dev,browser]" && playwright install chromium
cp .env.example .env            # set LOOM_ENV=dev; LLM_API_KEY only for real chat
loom doctor
python scripts/demo_gate5.py    # budget brake, backup/restore, eval summary
python scripts/demo_gate4_browser.py
loom eval
```

The demos are keyless (scripted models, local fixtures); the full README walk is in [the README](../README.md).
