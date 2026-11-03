# ADR-0074 — The desktop console exports tables through the main process and records each export locally

**Status:** Proposed
**Owner:** Max
**Issue:** #870
**Supersedes:** —
**Superseded by:** —

---

## Context

F28-01 asks for an export action every module shares: a role that may export
writes a file through the main process and a save dialog, the export is
audited, and a role that may not sees no export (DX-2). No contract defines who
may export, and no service accepts a record of an export: `audit-service` is
read-only (F16-09), and a new endpoint needs a contract and a service decision
first (ADR-0035).

## Decision

- **Who.** `ADMIN`, `ANALYST`, `MARKETING` and `AUDITOR` may export, and only a
  table of a module their role may already read (`x-mosaiq-roles`).
  `STORE_MANAGER` works the floor and may not. The list is
  `EXPORT_ROLES` in `clients/desktop/src/shared/modules.ts`; the main process
  enforces it and the renderer only hides the button (`canExport` in the state).
- **How.** The renderer sends the table it shows (module, column names, cell
  text) to the main process. The main process validates it, opens the save
  dialog, and writes a CSV: UTF-8 with a byte-order mark, RFC 4180 quoting, and a
  leading `=`, `+`, `-`, `@`, tab or carriage return defused with an apostrophe
  unless the cell is a plain number. The renderer never touches the file system.
- **Audit.** One JSON line per export is appended to `export-audit.log` in the
  app's data directory (time, user id, role, module, row count, file) before the
  file is written; an export that cannot be recorded is not written. Cell
  contents are never logged.

## Consequences

- The record is local to the machine. It cannot be read from the trail in
  `audit-service`. If the team wants exports in the server trail, a later ADR
  adds a contract and an endpoint, and this one is superseded.
- An export is exactly what is on screen: the filtered, current page.
- Exports that must repeat a data-origin label (RF-29, the experiment report)
  are the web system's CSV, not this one.

## Alternatives rejected

- **Every role that can read may export.** No policy of its own, so reading and
  copying out are the same permission, which they are not for a store floor role.
- **A new audit endpoint now.** Correct in the long run, but it needs a
  contract, a service and an ADR of its own before a client story may use it.
- **The renderer writes the file.** The renderer has no file access by design
  (`sandbox: true`, ADR-0041).
