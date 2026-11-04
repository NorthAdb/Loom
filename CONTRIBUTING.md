# Contributing

The repository is the source of truth for what is done, what is not, and what was decided. [docs/STATUS.md](docs/STATUS.md) has to match the tree. [docs/DECISIONS.md](docs/DECISIONS.md) has to match the choice. An external research page, canvas, or chat does not override either file.

## When you ship work

1. Update [docs/STATUS.md](docs/STATUS.md) in the same change.
2. Move the item out of **Not started** or **In progress** only as far as the tree justifies. **Done** means the behavior is in the repository, not that a doc describes it.
3. If you change a locked choice, add an ADR to [docs/DECISIONS.md](docs/DECISIONS.md) with the date. Update [docs/VISION.md](docs/VISION.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/ROADMAP.md](docs/ROADMAP.md), [docs/SECURITY.md](docs/SECURITY.md), and [README.md](README.md) when they would otherwise be wrong. The ADR in this repository is the decision. An external research page, canvas, or chat is not.
4. If people can run something new, replace the placeholder in the README “How to run” section.

## Scope of changes

Add code under `src/loom/` only when a [docs/ROADMAP.md](docs/ROADMAP.md) item needs it. Add a dependency in `pyproject.toml` only for that item.

New domain code goes in the Gate 6 package layout, not the root: `core/`, `memory/`, `tasks/`, `gateway/`, `tools/`, `inbound/`, `console/`, `audit/`, `ops/` under `src/loom/`. Do not add domain files at the `src/loom/` root; the root keeps only `__init__.py` and the compatibility shims that keep pre-Gate-6 import paths alive. Dependency direction: `cli` -> `core` -> the capability packages; `console` owns no business rules; `inbound` only creates tasks and scopes; `tools` never imports `console`.

Do not fork or vendor Coil. Do not add a Coil runtime. Do not make nanobot the shell. Do not add a mail-send path, a calendar write, or an unattended browser submit. See [docs/SECURITY.md](docs/SECURITY.md).
