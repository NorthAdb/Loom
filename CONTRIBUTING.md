# Contributing

The repository is the source of truth for what is done and what is not. [docs/STATUS.md](docs/STATUS.md) has to match the tree.

## When you ship work

1. Update [docs/STATUS.md](docs/STATUS.md) in the same change.
2. Move the item out of **Not started** or **In progress** only as far as the tree justifies. **Done** means the behavior is in the repository, not that a doc describes it.
3. If you change a locked choice, add an ADR to [docs/DECISIONS.md](docs/DECISIONS.md) with the date. Update [docs/VISION.md](docs/VISION.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/ROADMAP.md](docs/ROADMAP.md), [docs/SECURITY.md](docs/SECURITY.md), and [README.md](README.md) when they would otherwise be wrong.
4. If people can run something new, replace the placeholder in the README “How to run” section.

## Scope of early changes

Keep the package under `src/loom/` a skeleton until a [docs/ROADMAP.md](docs/ROADMAP.md) item needs code. Add a dependency in `pyproject.toml` only for that item.

Do not fork or vendor Coil. Do not add a Coil runtime. Do not make nanobot the shell. Do not add a mail-send path, a calendar write, or an unattended browser submit. See [docs/SECURITY.md](docs/SECURITY.md).
