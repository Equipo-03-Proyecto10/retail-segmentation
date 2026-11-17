import { randomUUID } from 'node:crypto';
import { readFile, stat } from 'node:fs/promises';
import { basename } from 'node:path';
import type {
  DuplicateKind,
  ErrorView,
  ImportFileView,
  ImportPhase,
  ImportRejectionQuery,
  ImportRejectionView,
  ImportRunView,
  ImportStatus,
  Paged,
  Result,
  SaleImportView,
} from '../shared/api';
import { localError, plain, type Backoffice } from './backoffice';
import { checked, id, invalidInput, pageNumber, pageOf, pageSize, record, Refused } from './checks';
import {
  chunkFields,
  ImportFileError,
  IMPORT_HEADER,
  MAX_FILE_BYTES,
  planImport,
  type ImportPlan,
  type PlannedChunk,
} from './import-chunks';
import { serviceNamespace, type Bundle } from './bundle';
import type { CallOptions, CallResult } from './transport';

// The sales import (F28-02, #871, ADR-0070). The CSV is read here, in the main process, from the
// file the user picks in a dialog; the renderer only ever sees its name, the number of rows and
// chunks, and the progress. The rows travel as numbered chunks of XML (150 rows or 60,000 bytes
// at most), each checked against sales.xsd by the transport before it is sent. Every call is
// refused here unless the signed-in role may call its operation (x-mosaiq-roles: ADMIN only).

const OPERATION = {
  create: 'createSaleImport',
  chunk: 'sendSaleImportChunk',
  get: 'getSaleImport',
  rejections: 'listSaleImportRejections',
  list: 'listSaleImports',
  cancel: 'cancelSaleImport',
} as const;

/** Between two chunks, so the edge's 10 requests a second (burst 20) is never what stops the import. */
const CHUNK_GAP_MS = 120;
/** The worker polls every 2 seconds (ADR-0070 section 3); asking more often shows nothing new. */
const POLL_MS = 2_000;
/** A transient failure is tried again after these waits: three attempts in all, with the same key and chunk. */
const RETRY_WAITS_MS = [1_000, 3_000];
const MAX_RETRY_AFTER_MS = 30_000;
/** Consecutive unreachable polls before the run pauses and offers to resume. */
const MAX_POLL_MISSES = 5;
const MAX_IMPORT_ID = Number.MAX_SAFE_INTEGER;

/** Refusals that no retry or resume can change: the import must be cancelled, or the file is not its file. */
const FINAL_CODES: ReadonlySet<string> = new Set([
  'chunk_conflict',
  'row_count_mismatch',
  'import_not_receiving',
  'import_finished',
  'sale_import_not_found',
  'forbidden_role',
  'invalid_body',
  'different_file',
]);

export interface ImportEnv {
  /** Opens the file dialog; undefined when the user cancels. */
  chooseFile(): Promise<string | undefined>;
  fileSize(path: string): Promise<number>;
  readFile(path: string): Promise<Buffer>;
  /** A fresh Idempotency-Key. */
  newKey(): string;
  sleep(ms: number): Promise<void>;
}

/** The real environment: the file is read from disk, and only the path the dialog returned is ever read. */
export function systemImportEnv(chooseFile: () => Promise<string | undefined>): ImportEnv {
  return {
    chooseFile,
    fileSize: async (path) => (await stat(path)).size,
    readFile: (path) => readFile(path),
    newKey: () => randomUUID(),
    sleep: (ms) => new Promise((done) => setTimeout(done, ms)),
  };
}

interface SaleImportXml {
  import_id: string;
  status: string;
  contract_version: string;
  total_rows: string;
  chunk_count: string;
  next_sequence: string;
  received: string;
  accepted: string;
  rejected: string;
  pending: string;
  created_at: string;
  finished_at?: string;
  created_by: string;
}

interface RejectionXml {
  line: string;
  field?: string;
  code: string;
  message: string;
  duplicate_of?: string;
  first_line?: string;
  sale_id?: string;
}

function importOf(x: SaleImportXml): SaleImportView {
  const view: SaleImportView = {
    import_id: Number(x.import_id),
    status: x.status as ImportStatus,
    contract_version: Number(x.contract_version),
    total_rows: Number(x.total_rows),
    chunk_count: Number(x.chunk_count),
    next_sequence: Number(x.next_sequence),
    received: Number(x.received),
    accepted: Number(x.accepted),
    rejected: Number(x.rejected),
    pending: Number(x.pending),
    created_at: x.created_at,
    created_by: x.created_by,
  };
  if (x.finished_at !== undefined && x.finished_at !== '') view.finished_at = x.finished_at;
  return view;
}

const ENTITY = /&(lt|gt|amp|quot|apos|#\d+|#x[0-9a-fA-F]+);/g;
const NAMED: Record<string, string> = { lt: '<', gt: '>', amp: '&', quot: '"', apos: "'" };

/** The parser leaves entities as written; a message quotes the file's own text, so it is decoded here, once. */
export function xmlText(text: string): string {
  return text.replace(ENTITY, (whole, name: string) => {
    if (name[0] !== '#') return NAMED[name] ?? whole;
    const code = name[1] === 'x' ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10);
    return code > 0 && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff)
      ? String.fromCodePoint(code)
      : whole;
  });
}

function rejectionOf(x: RejectionXml): ImportRejectionView {
  const view: ImportRejectionView = { line: Number(x.line), code: x.code, message: xmlText(x.message) };
  if (x.field !== undefined && x.field !== '') view.field = x.field;
  if (x.duplicate_of !== undefined) view.duplicate_of = x.duplicate_of as DuplicateKind;
  if (x.first_line !== undefined) view.first_line = Number(x.first_line);
  if (x.sale_id !== undefined) view.sale_id = Number(x.sale_id);
  return view;
}

type Step =
  { ok: true; res: Extract<CallResult, { ok: true }> } | { ok: false; error: ErrorView; status: number };
type Good = Extract<Step, { ok: true }>;
type Refusal = Extract<Step, { ok: false }>;

/** A failure that may pass on its own: no answer, a busy service, or the rate limit. */
function transient(step: Refusal): boolean {
  if (step.status === 0) return step.error.code === 'network_unreachable';
  return step.status === 429 || step.status === 502 || step.status === 503 || step.status === 504;
}

function waitFor(step: Refusal): number | undefined {
  const seconds = Number(step.error.retry_after);
  return Number.isFinite(seconds) && seconds > 0 ? Math.min(seconds * 1000, MAX_RETRY_AFTER_MS) : undefined;
}

const finished = (status: ImportStatus): boolean =>
  status === 'COMPLETED' || status === 'CANCELLED' || status === 'EXPIRED';

/** The file the user chose, parsed and cut into chunks, held here until the import is over. */
interface Prepared {
  name: string;
  size: number;
  plan: ImportPlan;
}

interface Run {
  phase: ImportPhase;
  fileName: string;
  totalRows: number;
  chunkCount: number;
  importId?: number;
  /** The next chunk this client sends. */
  next: number;
  staged: number;
  snapshot?: SaleImportView;
  error?: ErrorView;
  cancelRequested: boolean;
}

const fail = (error: ErrorView): Result<never> => ({ ok: false, error });

export class Imports {
  private file: Prepared | null = null;
  private run: Run | null = null;
  private owner: string | null = null;
  private readonly namespace: string;

  constructor(
    private readonly office: Backoffice,
    private readonly env: ImportEnv,
    bundle: Bundle,
  ) {
    this.namespace = serviceNamespace(bundle, 'sales');
  }

  // ---------- who may ----------

  /** What was chosen or started belongs to the user who did it. */
  private sync(): void {
    const who = this.office.userId();
    if (who !== this.owner) {
      this.owner = who;
      this.file = null;
      this.run = null;
    }
  }

  private denied(operation: string): ErrorView | null {
    const state = this.office.state();
    if (state.status !== 'signed_in') return localError('signed_out', 'You are signed out.');
    return state.operations.includes(operation)
      ? null
      : localError('forbidden_role', 'Your role may not import sales or read their data quality.');
  }

  private guarded<T>(operation: string, work: () => Promise<Result<T>>): Promise<Result<T>> {
    this.sync();
    const refusal = this.denied(operation);
    if (refusal !== null) return Promise.resolve(fail(refusal));
    return checked(this.office, work);
  }

  private active(): boolean {
    const phase = this.run?.phase;
    return phase === 'creating' || phase === 'sending' || phase === 'processing';
  }

  private idle(): void {
    if (this.active())
      throw new Refused(
        localError('import_running', 'An import is running. Wait for it to finish, or cancel it first.'),
      );
  }

  private held(): Prepared {
    if (this.file === null)
      throw new Refused(
        localError('no_file', 'Choose the CSV file first.', [{ field: 'file', code: 'required' }]),
      );
    return this.file;
  }

  // ---------- calls ----------

  private async call(options: Omit<CallOptions, 'bearer'>): Promise<Step> {
    const outcome = await this.office.attempt<Step>(async () => {
      const res = await this.office.authed(options);
      return {
        ok: true,
        value: res.ok ? { ok: true, res } : { ok: false, error: plain(res.error), status: res.status },
      };
    });
    return outcome.ok ? outcome.value : { ok: false, error: outcome.error, status: 0 };
  }

  /** Tries again after a transient failure, with the same request, so a retry never changes what was asked. */
  private async retrying(attempt: () => Promise<Step>, stop: () => boolean = () => false): Promise<Step> {
    let step = await attempt();
    for (const wait of RETRY_WAITS_MS) {
      if (step.ok || !transient(step) || stop()) return step;
      await this.env.sleep(waitFor(step) ?? wait);
      step = await attempt();
    }
    return step;
  }

  private putChunk(importId: number, chunk: PlannedChunk): Promise<Step> {
    return this.call({
      method: 'PUT',
      path: `/v1/sale-imports/${importId}/chunks/${chunk.sequence}`,
      body: { root: 'SaleImportChunk', fields: chunkFields(chunk.rows) },
    });
  }

  private getImportStep(importId: number): Promise<Step> {
    return this.call({ method: 'GET', path: `/v1/sale-imports/${importId}` });
  }

  private static viewOf(step: Good): SaleImportView {
    return importOf(step.res.data?.SaleImport as SaleImportXml);
  }

  // ---------- choosing the file ----------

  chooseFile(): Promise<Result<ImportFileView | null>> {
    return this.guarded(OPERATION.create, async () => {
      this.idle();
      const picked = await this.env.chooseFile();
      if (picked === undefined) return { ok: true, value: null };
      let bytes: Buffer;
      try {
        if ((await this.env.fileSize(picked)) > MAX_FILE_BYTES)
          throw new ImportFileError(
            'file_too_large',
            `The file is larger than ${MAX_FILE_BYTES / (1024 * 1024)} MiB. Split it into smaller files.`,
            [{ field: 'file', code: 'too_large' }],
          );
        bytes = await this.env.readFile(picked);
        // The file may have grown since its size was read.
        if (bytes.length > MAX_FILE_BYTES)
          throw new ImportFileError('file_too_large', 'The file grew while it was read. Choose it again.', [
            { field: 'file', code: 'too_large' },
          ]);
      } catch (e) {
        if (e instanceof ImportFileError) return fail(this.fileError(e));
        return fail(
          localError('invalid_file', 'The file could not be read.', [{ field: 'file', code: 'unreadable' }]),
        );
      }
      let plan: ImportPlan;
      try {
        plan = planImport(this.namespace, bytes);
      } catch (e) {
        if (e instanceof ImportFileError) return fail(this.fileError(e));
        throw e;
      }
      this.file = { name: basename(picked), size: bytes.length, plan };
      this.run = null;
      return {
        ok: true,
        value: {
          name: this.file.name,
          size_bytes: bytes.length,
          total_rows: plan.totalRows,
          chunk_count: plan.chunks.length,
          blank_lines: plan.blankLines,
        },
      };
    });
  }

  private fileError(e: ImportFileError): ErrorView {
    return localError('invalid_file', e.message, e.details);
  }

  // ---------- running an import ----------

  startImport(): Promise<Result<ImportRunView>> {
    return this.guarded(OPERATION.create, async () => {
      this.idle();
      const file = this.held();
      const run: Run = {
        phase: 'creating',
        fileName: file.name,
        totalRows: file.plan.totalRows,
        chunkCount: file.plan.chunks.length,
        next: 0,
        staged: 0,
        cancelRequested: false,
      };
      this.run = run;
      // One key for this start, kept across retries: a create that timed out may have succeeded.
      const key = this.env.newKey();
      const step = await this.retrying(() =>
        this.call({
          method: 'POST',
          path: '/v1/sale-imports',
          headers: { 'Idempotency-Key': key },
          body: {
            root: 'SaleImportRequest',
            fields: {
              contract_version: '1',
              header: [...IMPORT_HEADER],
              total_rows: String(run.totalRows),
              chunk_count: String(run.chunkCount),
            },
          },
        }),
      );
      if (!step.ok) {
        this.run = null;
        return fail(step.error);
      }
      const view = Imports.viewOf(step);
      run.importId = view.import_id;
      run.snapshot = view;
      // A fresh start sends from chunk 0 even when a replayed create says more were staged: a chunk
      // the service already holds is answered as a replay, and a different one as chunk_conflict.
      run.staged = view.next_sequence;
      run.phase = 'sending';
      void this.drive(run, file);
      return { ok: true, value: this.progress(run) };
    });
  }

  /**
   * Continues an import where the service says it stands. The file is the same one when the
   * service accepts the last chunk it staged again as a replay: another file's chunk would be a
   * conflict (ADR-0070 section 1).
   */
  resumeImport(importId: unknown): Promise<Result<ImportRunView>> {
    return this.guarded(OPERATION.chunk, async () => {
      this.idle();
      const iid = id(importId, MAX_IMPORT_ID, 'The import');
      const file = this.held();
      const got = await this.retrying(() => this.getImportStep(iid));
      if (!got.ok) return fail(got.error);
      const view = Imports.viewOf(got);
      if (view.status !== 'RECEIVING' && view.status !== 'PROCESSING')
        return fail(
          localError(
            'import_not_resumable',
            `Import ${iid} is ${view.status}; there is nothing left to send.`,
          ),
        );
      if (view.total_rows !== file.plan.totalRows || view.chunk_count !== file.plan.chunks.length)
        return fail(this.differentFile(iid));
      if (view.status === 'RECEIVING' && view.next_sequence > 0) {
        const last = file.plan.chunks[view.next_sequence - 1];
        if (last === undefined) return fail(this.differentFile(iid));
        const again = await this.retrying(() => this.putChunk(iid, last));
        if (!again.ok)
          return fail(again.error.code === 'chunk_conflict' ? this.differentFile(iid) : again.error);
      }
      const run: Run = {
        phase: 'sending',
        fileName: file.name,
        totalRows: file.plan.totalRows,
        chunkCount: file.plan.chunks.length,
        importId: iid,
        next: view.next_sequence,
        staged: view.next_sequence,
        snapshot: view,
        cancelRequested: false,
      };
      this.run = run;
      void this.drive(run, file);
      return { ok: true, value: this.progress(run) };
    });
  }

  private differentFile(importId: number): ErrorView {
    return localError(
      'different_file',
      `This is not the file import ${importId} was opened with. Choose the same file, unchanged.`,
      [{ field: 'file', code: 'different_file' }],
    );
  }

  private async drive(run: Run, file: Prepared): Promise<void> {
    try {
      let sent = 0;
      while (run.next < file.plan.chunks.length) {
        if (run.cancelRequested) return;
        const chunk = file.plan.chunks[run.next] as PlannedChunk;
        if (sent > 0) await this.env.sleep(CHUNK_GAP_MS);
        const step = await this.retrying(
          () => this.putChunk(run.importId as number, chunk),
          () => run.cancelRequested,
        );
        if (run.cancelRequested) return;
        if (!step.ok) return void (await this.halt(run, step.error));
        const view = Imports.viewOf(step);
        run.snapshot = view;
        run.staged = view.next_sequence;
        run.next = chunk.sequence + 1;
        sent += 1;
      }
      await this.follow(run);
    } catch {
      if (!run.cancelRequested)
        await this.halt(run, localError('client_error', 'The import stopped unexpectedly.'));
    }
  }

  /** Every chunk is staged: the worker applies the rows, and the counts are read until the import ends. */
  private async follow(run: Run): Promise<void> {
    run.phase = 'processing';
    let misses = 0;
    while (!run.cancelRequested) {
      const step = await this.getImportStep(run.importId as number);
      if (run.cancelRequested) return;
      if (step.ok) {
        misses = 0;
        const view = Imports.viewOf(step);
        run.snapshot = view;
        run.staged = view.next_sequence;
        if (finished(view.status)) {
          this.end(run);
          return;
        }
      } else if (!transient(step) || (misses += 1) >= MAX_POLL_MISSES) {
        return void (await this.halt(run, step.error));
      }
      await this.env.sleep(POLL_MS);
    }
  }

  /** The run stops here, either to be resumed or because the service refused it for good. */
  private async halt(run: Run, error: ErrorView): Promise<void> {
    // ADR-0070 section 1: a client treats row_count_mismatch as final, cancels and opens a new import.
    if (error.code === 'row_count_mismatch' && run.importId !== undefined) {
      const cancelled = await this.call(Imports.cancelRequest(run.importId));
      if (cancelled.ok) run.snapshot = Imports.viewOf(cancelled);
    }
    run.error = error;
    run.phase = FINAL_CODES.has(error.code) ? 'failed' : 'paused';
  }

  private end(run: Run): void {
    run.phase = 'finished';
    // The rows were held only to send them.
    if (this.run === run) this.file = null;
  }

  private static cancelRequest(importId: number): Omit<CallOptions, 'bearer'> {
    return {
      method: 'PUT',
      path: `/v1/sale-imports/${importId}/status`,
      body: { root: 'SaleImportStatusRequest', fields: { status: 'CANCELLED' } },
    };
  }

  cancelImport(importId: unknown): Promise<Result<SaleImportView>> {
    return this.guarded(OPERATION.cancel, async () => {
      const iid = id(importId, MAX_IMPORT_ID, 'The import');
      const run = this.run?.importId === iid ? this.run : undefined;
      if (run !== undefined) run.cancelRequested = true;
      const step = await this.retrying(() => this.call(Imports.cancelRequest(iid)));
      if (!step.ok) {
        if (run !== undefined) {
          run.cancelRequested = false;
          run.error = step.error;
          run.phase = 'paused';
        }
        return fail(step.error);
      }
      const view = Imports.viewOf(step);
      if (run !== undefined) {
        run.snapshot = view;
        run.staged = view.next_sequence;
        run.error = undefined;
        this.end(run);
      }
      return { ok: true, value: view };
    });
  }

  private progress(run: Run): ImportRunView {
    const view: ImportRunView = {
      phase: run.phase,
      file_name: run.fileName,
      total_rows: run.totalRows,
      chunk_count: run.chunkCount,
      chunks_staged: run.staged,
      can_resume: run.phase === 'paused' && this.file !== null,
    };
    if (run.snapshot !== undefined) view.import = run.snapshot;
    if (run.error !== undefined) view.error = run.error;
    return view;
  }

  /** The import this console is running, or null. It asks no service: the run reads them. */
  importProgress(): Promise<Result<ImportRunView | null>> {
    this.sync();
    const refusal = this.denied(OPERATION.get);
    if (refusal !== null) return Promise.resolve(fail(refusal));
    return Promise.resolve({ ok: true, value: this.run === null ? null : this.progress(this.run) });
  }

  // ---------- reading imports ----------

  listImports(query: unknown): Promise<Result<Paged<SaleImportView>>> {
    return this.guarded(OPERATION.list, async () => {
      const q = record(query ?? {}, 'The search');
      const params = new URLSearchParams({
        page: String(pageNumber(q.page)),
        size: String(pageSize(q.size)),
      });
      const step = await this.call({ method: 'GET', path: `/v1/sale-imports?${params.toString()}` });
      if (!step.ok) return fail(step.error);
      const data = step.res.data?.SaleImports as { SaleImport?: SaleImportXml[] } & Record<string, unknown>;
      return { ok: true, value: { items: (data.SaleImport ?? []).map(importOf), page: pageOf(data) } };
    });
  }

  getImport(importId: unknown): Promise<Result<SaleImportView>> {
    return this.guarded(OPERATION.get, async () => {
      const iid = id(importId, MAX_IMPORT_ID, 'The import');
      const step = await this.getImportStep(iid);
      return step.ok ? { ok: true, value: Imports.viewOf(step) } : fail(step.error);
    });
  }

  listRejections(query: unknown): Promise<Result<Paged<ImportRejectionView>>> {
    return this.guarded(OPERATION.rejections, async () => {
      const q = record(query, 'The search') as Partial<ImportRejectionQuery> & Record<string, unknown>;
      if (q.import_id === undefined) throw invalidInput('The import is missing.');
      const iid = id(q.import_id, MAX_IMPORT_ID, 'The import');
      const params = new URLSearchParams({
        page: String(pageNumber(q.page)),
        size: String(pageSize(q.size)),
      });
      const step = await this.call({
        method: 'GET',
        path: `/v1/sale-imports/${iid}/rejections?${params.toString()}`,
      });
      if (!step.ok) return fail(step.error);
      const data = step.res.data?.SaleImportRejections as {
        SaleImportRejection?: RejectionXml[];
      } & Record<string, unknown>;
      return {
        ok: true,
        value: { items: (data.SaleImportRejection ?? []).map(rejectionOf), page: pageOf(data) },
      };
    });
  }
}
