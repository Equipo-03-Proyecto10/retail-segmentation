@AGENTS.md
@~/.claude/rs-local.md

> The `AGENTS.md` imported above governs **this repository only**. The Obsidian
> vault referenced by `rs-local.md` has its own `AGENTS.md` and `CLAUDE.md` at
> its root — different files, identical names — and they carry rules this one
> does not, including a git pull/push workflow. Read them before touching the
> vault.

## Claude Code specifics

- Use plan mode for anything touching the schema, the authorization middleware,
  or the single-administrator rule. Those are the places where a wrong change is
  expensive to unwind.
- When a task produces a decision, record it as an ADR in `docs/adr/` or as a
  comment on the GitHub issue. The repository is the authoritative record.
- Prefer editing existing files over creating new ones, and do not create a
  directory until there is a file to put in it. Empty directories are not
  committed.
