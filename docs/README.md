# Docs

The repository is the source of truth for progress and for locked decisions. An external research page, canvas, or chat may inform a change. It does not replace these files. If it and a file here disagree, the file here wins ([ADR-017](DECISIONS.md)).

| File | Owns |
| --- | --- |
| [STATUS.md](STATUS.md) | What the tree contains. Done, in progress, not started, and the Gate 1–5 map. Moves in the same change as the code. |
| [ROADMAP.md](ROADMAP.md) | What comes next: Gate 6 onward, priority, and the hard boundaries. Not a record of what shipped. |
| [DECISIONS.md](DECISIONS.md) | Locked choices (ADRs) and their consequences. Intent, not implementation. |
| [VISION.md](VISION.md) | Product intent, the Gate 1–5 loop that already exists, and the boundaries that do not move. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | The loop that runs today — through Gate 9 Passed and Gate 8 feature code (Gate 8 not Passed; dogfood open) — and the Gate 10+ target. |
| [SECURITY.md](SECURITY.md) | Constraints every change has to satisfy, noting which Gate 6+ rules are implemented and which still bind future code. |
| [RESUME_STORY.md](RESUME_STORY.md) | What a reader can reproduce from this tree today. Gate 9 is Passed on main; Gate 8 is not. |
| [../README.md](../README.md) | How to run what exists, and the one-line current position. |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | A change updates STATUS, and a changed decision updates the ADR. |

Which file to edit:

- You shipped behavior. Update STATUS. Do not mark it done in a roadmap bullet alone.
- You changed a locked choice. Add an ADR, then fix VISION, ARCHITECTURE, ROADMAP, SECURITY, and the README where they would still say the old thing.
- You are describing a future gate. Put it in ROADMAP. Do not write it into STATUS **Done** or into RESUME_STORY.
