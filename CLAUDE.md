@AGENTS.md
@~/.claude/rs-local.md

## Claude Code specifics

- Use plan mode for anything touching the schema, the authorization middleware,
  or the single-administrator rule. Those are the places where a wrong change is
  expensive to unwind.
- When a task produces a decision, record it as an ADR in `docs/adr/` or as a
  comment on the GitHub issue. The repository is the authoritative record.
- Prefer editing existing files over creating new ones, and do not create a
  directory until there is a file to put in it. Empty directories are not
  committed.
