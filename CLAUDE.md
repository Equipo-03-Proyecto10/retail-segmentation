@AGENTS.md
@~/.claude/rs-local.md

## Claude Code specifics

- Use plan mode for anything touching `customer_segment_assignment`,
  `segmentation_model_run`, or the authentication flow. Those are the two places
  where a wrong change is expensive to unwind.
- When a task produces a decision, record it as an ADR in `docs/adr/` or as a
  comment on the GitHub issue. The repository is the authoritative record.
- Prefer editing existing files over creating new ones. This repo has a fixed
  structure documented in `README.md`.
