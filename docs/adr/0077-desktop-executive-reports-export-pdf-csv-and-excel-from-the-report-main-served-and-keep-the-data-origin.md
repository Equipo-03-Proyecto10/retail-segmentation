# ADR-0077 — Desktop executive reports export PDF, CSV and Excel from the report main served, and keep the data origin

**Status:** Accepted (2026-11-09)
**Owner:** Max
**Issue:** #883
**Supersedes:** in part [ADR-0074](0074-the-desktop-console-exports-through-the-main-process-and-records-each-export-locally.md) (for the Reports module only: the file formats, where the exported figures come from, and that a desktop export repeats no data-origin label)
**Superseded by:** —

---

## Context

F28-14 (#883) adds executive reports to the desktop console. Each report is one
analytics-kpis figure over a period, with filters, a summary, a chart, the
figures table and each metric's definition. The issue asks for three things:
- an allowed role exports PDF, CSV and Excel files whose figures match the screen;
- a CSV cell that starts with a formula character is neutralised;
- synthetic data stays marked (ADR-0052).

ADR-0074 allows less than that, and three of its clauses stand in the way:
- **Format.** The main process "writes a CSV".
- **Source.** The renderer "sends the table it shows", and a screen with several tables exports only the first.
- **Origin label.** Exports that must repeat a data-origin label "are the web system's CSV, not this one".

A report is not one table: its summary and definitions are part of what a reader
needs to interpret it. And the issue requires the synthetic marker in the file.

## Decision

- **Formats.** The Reports module exports PDF, CSV and Excel (`.xlsx`). Every
  other module keeps ADR-0074's CSV of its main table.
- **Source of the figures.** The main process exports the report it last served
  to that user, from the XSD-validated XML of `GET /v1/kpi-reports/{kpi}`. The
  renderer sends only the format (`reports.ts`, `exportReport`). The renderer
  cannot alter a figure, so the file matches what main served, and the screen
  renders the same report through the same function (`report-table.ts`).
- **Content.** One report file holds the summary, the figures table and the
  definitions:
  - the PDF has them as sections;
  - the workbook has them as three sheets;
  - the CSV holds the figures table only.

  A report holds at most 5,000 rows. A larger one is cut, and the PDF and Excel summaries say so.
- **Data origin.** Every file carries a per-row `Data origin` column. The PDF
  carries the screen's synthetic banner, the suggested file name ends in
  `-synthetic`, and the audit line records `synthetic` (ADR-0052).
- **Formula safety.** The CSV reuses ADR-0074's rule. The workbook stores text
  as inline strings, so nothing is ever a formula, and writes only plain numbers
  as numbers.
- **Writers.** The PDF and the `.xlsx` are written by hand in
  `report-files.ts`, about 370 lines on `node:zlib`, with no new dependency.

Everything else in ADR-0074 stands for this module too:
- the export roles (`EXPORT_ROLES`), checked in main;
- the save dialog as the only file written;
- the audit line appended before the file is written, with no cell contents;
- the renderer never touching the file system.

## Consequences

- **Fidelity.** An exported report is exactly what main served, so it cannot
  disagree with the service. If two report requests overlap, main must keep only
  the newest. Otherwise the file is the report that finished last, not the one
  on screen.
- **CSV and the cut.** The CSV is the figures table only. A cut report's CSV
  carries no summary row, so it does not say it was cut; the PDF and the
  workbook do. Its per-row `Data origin` column is what ADR-0052 needs.
- **Upkeep.** The hand-written writers are code the team maintains. They are
  deterministic and small, and they add no supply-chain surface. A format
  feature beyond text, rectangles and sheets (charts in the workbook, fonts
  beyond Helvetica) would reopen the choice of a library.
- **Scope.** ADR-0074's rule that a desktop export repeats no data-origin label
  no longer holds for reports. It still holds for the other modules, whose
  tables carry no origin.

## Alternatives rejected

- **The renderer sends the report table, as ADR-0074 does.** The renderer could
  then send figures that main never served. It would also send the summary and
  definitions as free text that main would have to trust.
- **A PDF and spreadsheet library.** It would be far larger than the use, and
  each package would need a review and a pin. The output of the hand-written
  writers is checked by the unit tests and read back.
- **CSV only, as ADR-0074 decides.** That fails #883's second criterion.
- **Leave the data-origin label to the web.** That fails #883's fourth
  criterion and ADR-0052 for a report read away from the web.

## Compliance

- **Unit tests** (`reports.test.ts`):
  - all three formats are written from one served report;
  - one audit line is written per export, before the file;
  - a formula cell is neutralised;
  - a synthetic report keeps its `Data origin` column, its summary line and its
    `-synthetic` file name.
- **`check:adr41`** still passes. The renderer gains only the three bridge
  methods that `app.spec.ts` pins.
