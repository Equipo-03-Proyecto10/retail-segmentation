import { join } from 'node:path';
import type { ExportOutcome, ExportRequest, Result } from '../shared/api';
import { MODULE_GROUPS, mayExport } from '../shared/modules';
import { localError, type Backoffice } from './backoffice';

// A table leaves the app only through here (F28-01, ADR-0074): the role is checked in the main
// process, the user picks the file in a save dialog, and one audit line is written before the file.

const MAX_ROWS = 100_000;
const MAX_COLUMNS = 100;
const MAX_CELL = 10_000;

export interface ExportEnv {
  /** Opens the save dialog; undefined when the user cancels. */
  chooseFile(suggestedName: string): Promise<string | undefined>;
  saveCsv(path: string, text: string): void;
  appendLine(path: string, line: string): void;
  now(): Date;
}

/** Lets spreadsheets read the file as UTF-8. */
const BOM = String.fromCharCode(0xfeff);

const FORMULA_START = /^[=+\-@\t\r]/;
const PLAIN_NUMBER = /^-?\d+(\.\d+)?$/;

/** One CSV cell: quoted when needed, and a leading formula character is defused for spreadsheets. */
export function csvCell(value: string): string {
  const safe = FORMULA_START.test(value) && !PLAIN_NUMBER.test(value) ? `'${value}` : value;
  return /[",\r\n]/.test(safe) ? `"${safe.replaceAll('"', '""')}"` : safe;
}

export function toCsv(columns: string[], rows: string[][]): string {
  const lines = [columns, ...rows].map((r) => r.map(csvCell).join(','));
  return `${BOM}${lines.join('\r\n')}\r\n`;
}

function validRequest(raw: unknown): ExportRequest | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const r = raw as Record<string, unknown>;
  const strings = (v: unknown): v is string[] =>
    Array.isArray(v) && v.every((x) => typeof x === 'string' && x.length <= MAX_CELL);
  if (typeof r.module !== 'string' || !strings(r.columns) || !Array.isArray(r.rows)) return null;
  if (r.columns.length === 0 || r.columns.length > MAX_COLUMNS || r.rows.length > MAX_ROWS) return null;
  const width = r.columns.length;
  if (!r.rows.every((row) => strings(row) && row.length === width)) return null;
  return { module: r.module, columns: r.columns, rows: r.rows as string[][] };
}

export class Exporter {
  constructor(
    private readonly office: Backoffice,
    private readonly env: ExportEnv,
    private readonly auditDir: string,
  ) {}

  async exportTable(raw: unknown): Promise<Result<ExportOutcome>> {
    const who = this.office.identity();
    if (who === null) return fail('signed_out', 'You are signed out.');
    const request = validRequest(raw);
    if (request === null) return fail('invalid_input', 'The table could not be exported.');
    const found = MODULE_GROUPS.flatMap((g) => g.modules).find((m) => m.name === request.module);
    const state = this.office.state();
    const readable =
      found !== undefined &&
      state.status === 'signed_in' &&
      (found.operation === null || state.operations.includes(found.operation));
    if (!mayExport(who.role) || !readable)
      return fail('forbidden_role', 'Your role may not export this table.');

    const stamp = this.env.now().toISOString();
    const file = await this.env.chooseFile(`mosaiq-${request.module}-${stamp.slice(0, 10)}.csv`);
    if (file === undefined) return { ok: true, value: null };

    const record = { at: stamp, user_id: who.userId, role: who.role, module: request.module };
    const log = join(this.auditDir, 'export-audit.log');
    try {
      // Audit first: an export that cannot be recorded is not written.
      this.env.appendLine(log, `${JSON.stringify({ ...record, rows: request.rows.length, file })}\n`);
    } catch {
      return fail('audit_unavailable', 'The export was not written because it could not be recorded.');
    }
    try {
      this.env.saveCsv(file, toCsv(request.columns, request.rows));
    } catch {
      try {
        this.env.appendLine(log, `${JSON.stringify({ ...record, outcome: 'write_failed', file })}\n`);
      } catch {
        // The first line already says an export was attempted.
      }
      return fail('write_failed', 'The file could not be written.');
    }
    return { ok: true, value: { rows: request.rows.length, file } };
  }
}

function fail(code: string, message: string): Result<never> {
  return { ok: false, error: localError(code, message) };
}
