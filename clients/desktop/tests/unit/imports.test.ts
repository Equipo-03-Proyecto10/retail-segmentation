import { describe, expect, it } from 'vitest';
import { operationsForRole, serviceNamespace } from '../../src/main/bundle';
import { chunkXml, planImport } from '../../src/main/import-chunks';
import { Imports, xmlText, type ImportEnv } from '../../src/main/imports';
import {
  bundle,
  claims,
  deferred,
  errorXml,
  example,
  makeOffice,
  MemoryVault,
  tokenResult,
} from '../support/fake';
import type { Reply, Responder, Seen } from '../support/fake';
import { chunkLines, importXml, salesCsv } from '../support/imports';
import type { ImportRunView } from '../../src/shared/api';

// The import module's main-process side (F28-02, #871), against the recorded examples of
// contracts/sales/: choosing and checking the file, creating the import, sending numbered
// chunks, resuming, cancelling, and reading the report.

const NS = serviceNamespace(bundle, 'sales');
const path = (s: Seen): string => `${s.method} ${new URL(s.url).pathname.replace('/api/sales', '')}`;

/** A sales-service that keeps one import, stages chunks as the contract says and applies rows when asked. */
class FakeSales {
  total = 0;
  chunkCount = 0;
  staged = 0;
  received = 0;
  accepted = 0;
  rejected = 0;
  pending = 0;
  status = 'RECEIVING';
  bodies: (string | undefined)[] = [];
  gates = new Map<number, Promise<void>>();
  /** Answers to give instead of the normal one, per request, until it returns undefined. */
  interfere: (req: Seen) => Reply | undefined = () => undefined;

  xml(): string {
    const done = this.status !== 'RECEIVING' && this.status !== 'PROCESSING';
    return importXml(example('sales', done ? 'get-sale-import-completed' : 'get-sale-import'), {
      status: this.status,
      total_rows: this.total,
      chunk_count: this.chunkCount,
      next_sequence: this.staged,
      received: this.received,
      accepted: this.accepted,
      rejected: this.rejected,
      pending: this.pending,
    });
  }

  private work(): void {
    if (this.status !== 'PROCESSING') return;
    const n = Math.min(this.pending, 200);
    const refused = Math.floor(n / 10);
    this.accepted += n - refused;
    this.rejected += refused;
    this.pending -= n;
    if (this.pending === 0) this.status = 'COMPLETED';
  }

  readonly respond: Responder = async (req) => {
    const custom = this.interfere(req);
    if (custom !== undefined) return custom;
    const p = path(req);
    if (p === 'POST /v1/sale-imports') {
      const m = /<total_rows>(\d+)<\/total_rows><chunk_count>(\d+)</.exec(req.body ?? '');
      this.total = Number(m?.[1]);
      this.chunkCount = Number(m?.[2]);
      return { status: 201, body: this.xml() };
    }
    const chunk = /^PUT \/v1\/sale-imports\/17\/chunks\/(\d+)$/.exec(p);
    if (chunk) {
      const seq = Number(chunk[1]);
      await this.gates.get(seq);
      if (this.status !== 'RECEIVING') return { status: 409, body: example('sales', 'import-not-receiving') };
      if (seq < this.staged)
        return this.bodies[seq] === req.body
          ? { status: 200, body: this.xml(), headers: { 'Idempotent-Replayed': 'true' } }
          : { status: 409, body: example('sales', 'chunk-conflict') };
      if (seq > this.staged) return { status: 409, body: example('sales', 'chunk-out-of-order') };
      const rows = chunkLines(req.body).length;
      this.bodies[seq] = req.body;
      this.staged += 1;
      this.received += rows;
      this.pending += rows;
      if (this.staged === this.chunkCount) this.status = 'PROCESSING';
      return { status: 202, body: this.xml() };
    }
    if (p === 'GET /v1/sale-imports/17') {
      this.work();
      return { status: 200, body: this.xml() };
    }
    if (p === 'PUT /v1/sale-imports/17/status') {
      this.status = 'CANCELLED';
      return { status: 200, body: this.xml() };
    }
    return { status: 404, body: example('sales', 'sale-import-not-found') };
  };
}

interface Rig {
  imports: Imports;
  seen: Seen[];
  slept: number[];
  keys: string[];
  picked: () => number;
}

async function rig(
  role: string,
  responder: Responder,
  file: string | Buffer | null = salesCsv(320),
  choose: string | null = '/home/user/sales.csv',
): Promise<Rig> {
  const { office, seen } = makeOffice(
    (r) => {
      if (r.method === 'POST' && r.url.endsWith('/v1/sessions'))
        return { status: 201, body: tokenResult('a1', 'r1') };
      if (r.url.endsWith('/v1/sessions/current'))
        return r.method === 'GET' ? { status: 200, body: claims(role) } : { status: 204 };
      return responder(r);
    },
    new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') }),
  );
  await office.signIn({ email: 'a@b.c', password: 'pw' });
  seen.length = 0;
  const slept: number[] = [];
  const keys: string[] = [];
  let picked = 0;
  const bytes = file === null ? Buffer.alloc(0) : Buffer.from(file);
  const env: ImportEnv = {
    chooseFile: () => {
      picked += 1;
      return Promise.resolve(choose ?? undefined);
    },
    fileSize: () => Promise.resolve(bytes.length),
    readFile: () => Promise.resolve(bytes),
    newKey: () => {
      keys.push(`key-${keys.length + 1}`);
      return keys.at(-1) as string;
    },
    sleep: (ms) => {
      slept.push(ms);
      return Promise.resolve();
    },
  };
  return { imports: new Imports(office, env, bundle), seen, slept, keys, picked: () => picked };
}

async function until(imports: Imports, done: (v: ImportRunView) => boolean): Promise<ImportRunView> {
  for (const deadline = Date.now() + 20_000; Date.now() < deadline;) {
    const r = await imports.importProgress();
    if (r.ok && r.value !== null && done(r.value)) return r.value;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error('the run never reached the expected state');
}

const sent = (seen: Seen[]): string[] => seen.map(path);
const chunkPuts = (seen: Seen[]): Seen[] => seen.filter((s) => /\/chunks\/\d+$/.test(s.url));

describe('choosing the file', () => {
  it('reads it in the main process and returns only its name, rows and chunks', async () => {
    const { imports, seen } = await rig('ADMIN', new FakeSales().respond);
    const res = await imports.chooseFile();
    expect(res).toEqual({
      ok: true,
      value: {
        name: 'sales.csv',
        size_bytes: salesCsv(320).length,
        total_rows: 320,
        chunk_count: 3,
        blank_lines: 0,
      },
    });
    expect(seen).toEqual([]);
  });

  it('returns null when the dialog is closed', async () => {
    const { imports } = await rig('ADMIN', new FakeSales().respond, salesCsv(3), null);
    expect(await imports.chooseFile()).toEqual({ ok: true, value: null });
  });

  it('refuses a wrong header, naming its column, and sends nothing', async () => {
    const { imports, seen } = await rig(
      'ADMIN',
      new FakeSales().respond,
      salesCsv(3).replace('channel_id', 'channel'),
    );
    expect(await imports.chooseFile()).toMatchObject({
      ok: false,
      error: { code: 'invalid_file', details: [{ field: 'header[3]', code: 'unexpected_column' }] },
    });
    expect(await imports.startImport()).toMatchObject({ ok: false, error: { code: 'no_file' } });
    expect(seen).toEqual([]);
  });

  it('refuses a file that is too large before reading it', async () => {
    const { imports } = await rig('ADMIN', new FakeSales().respond, salesCsv(3));
    const big = new Imports(
      (imports as unknown as { office: never }).office,
      {
        chooseFile: () => Promise.resolve('/x.csv'),
        fileSize: () => Promise.resolve(17 * 1024 * 1024),
        readFile: () => Promise.reject(new Error('must not be read')),
        newKey: () => 'k',
        sleep: () => Promise.resolve(),
      },
      bundle,
    );
    expect(await big.chooseFile()).toMatchObject({
      ok: false,
      error: { code: 'invalid_file', details: [{ field: 'file', code: 'too_large' }] },
    });
  });

  it('reports a file it cannot read', async () => {
    const { imports } = await rig('ADMIN', new FakeSales().respond, salesCsv(3));
    const broken = new Imports(
      (imports as unknown as { office: never }).office,
      {
        chooseFile: () => Promise.resolve('/x.csv'),
        fileSize: () => Promise.reject(new Error('ENOENT')),
        readFile: () => Promise.reject(new Error('ENOENT')),
        newKey: () => 'k',
        sleep: () => Promise.resolve(),
      },
      bundle,
    );
    expect(await broken.chooseFile()).toMatchObject({ ok: false, error: { code: 'invalid_file' } });
  });
});

describe('roles', () => {
  const others = ['ANALYST', 'MARKETING', 'STORE_MANAGER', 'INVENTORY_PLANNER', 'AUDITOR'];

  it('offers the import to the administrator only (x-mosaiq-roles)', () => {
    for (const op of [
      'createSaleImport',
      'sendSaleImportChunk',
      'getSaleImport',
      'listSaleImportRejections',
      'listSaleImports',
      'cancelSaleImport',
    ]) {
      expect(operationsForRole(bundle, 'ADMIN')).toContain(op);
      for (const role of others) expect(operationsForRole(bundle, role)).not.toContain(op);
    }
  });

  it.each(others)(
    'refuses every call of %s in the main process, opens no dialog and sends nothing',
    async (role) => {
      const { imports, seen, picked } = await rig(role, new FakeSales().respond);
      const refusals = [
        await imports.chooseFile(),
        await imports.startImport(),
        await imports.resumeImport(17),
        await imports.importProgress(),
        await imports.cancelImport(17),
        await imports.listImports({}),
        await imports.getImport(17),
        await imports.listRejections({ import_id: 17 }),
      ];
      for (const r of refusals) expect(r).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
      expect(picked()).toBe(0);
      expect(seen).toEqual([]);
    },
  );
});

describe('loading a file', () => {
  it('opens the import, sends chunks 0 to 2 in order as XML and reconciles the counts', async () => {
    const sales = new FakeSales();
    const { imports, seen, keys } = await rig('ADMIN', sales.respond);
    await imports.chooseFile();
    const started = await imports.startImport();
    expect(started).toMatchObject({
      ok: true,
      value: { file_name: 'sales.csv', total_rows: 320, chunk_count: 3 },
    });
    const end = await until(imports, (v) => v.phase === 'finished');

    const create = seen[0] as Seen;
    expect(path(create)).toBe('POST /v1/sale-imports');
    expect(create.headers['Idempotency-Key']).toBe('key-1');
    expect(create.headers['Content-Type']).toBe('application/xml');
    expect(create.headers.Accept).toBe('application/xml');
    expect(create.body).toContain('<contract_version>1</contract_version>');
    expect(create.body).toContain('<total_rows>320</total_rows><chunk_count>3</chunk_count>');
    expect(create.body?.match(/<header>/g)).toHaveLength(8);
    expect(create.body).toContain('<header>transaction_id</header><header>customer_id</header>');
    expect(keys).toEqual(['key-1']);

    const puts = chunkPuts(seen);
    expect(puts.map(path)).toEqual([
      'PUT /v1/sale-imports/17/chunks/0',
      'PUT /v1/sale-imports/17/chunks/1',
      'PUT /v1/sale-imports/17/chunks/2',
    ]);
    expect(puts.map((p) => chunkLines(p.body).length)).toEqual([150, 150, 20]);
    expect(chunkLines(puts[0]?.body)[0]).toBe(2);
    expect(chunkLines(puts[2]?.body)[0]).toBe(302);
    for (const p of puts) {
      expect(p.headers['Content-Type']).toBe('application/xml');
      expect(Buffer.byteLength(p.body as string)).toBeLessThanOrEqual(60_000);
    }

    expect(end.import).toMatchObject({ status: 'COMPLETED', total_rows: 320, received: 320, pending: 0 });
    const i = end.import as NonNullable<typeof end.import>;
    expect(i.accepted + i.rejected + i.pending).toBe(i.received);
    expect(end.chunks_staged).toBe(3);
  });

  it('shows the staged chunks and the counts while the next chunk is on its way', async () => {
    const sales = new FakeSales();
    const gate = deferred<void>();
    sales.gates.set(1, gate.promise);
    const { imports } = await rig('ADMIN', sales.respond);
    await imports.chooseFile();
    await imports.startImport();
    const mid = await until(imports, (v) => v.chunks_staged === 1);
    expect(mid).toMatchObject({
      phase: 'sending',
      chunk_count: 3,
      import: { status: 'RECEIVING', received: 150, pending: 150 },
    });
    gate.resolve();
    const end = await until(imports, (v) => v.phase === 'finished');
    expect(end.import?.status).toBe('COMPLETED');
  });

  it('shows the counts moving while the service applies the rows, and they add up each time', async () => {
    const sales = new FakeSales();
    const { imports } = await rig('ADMIN', sales.respond, salesCsv(500));
    await imports.chooseFile();
    await imports.startImport();
    const seenCounts = new Set<string>();
    let last: ImportRunView | undefined;
    for (const deadline = Date.now() + 20_000; last?.phase !== 'finished' && Date.now() < deadline;) {
      const r = await imports.importProgress();
      if (r.ok && r.value?.import !== undefined) {
        last = r.value;
        const v = r.value.import;
        expect(v.accepted + v.rejected + v.pending).toBe(v.received);
        seenCounts.add(`${v.accepted}/${v.rejected}/${v.pending}`);
      }
      await new Promise((resolve) => setTimeout(resolve, 2));
    }
    expect(last?.phase).toBe('finished');
    expect(seenCounts.size).toBeGreaterThan(2);
  });

  it('paces the chunks and reads the counts every two seconds until the end', async () => {
    const { imports, slept } = await rig('ADMIN', new FakeSales().respond);
    await imports.chooseFile();
    await imports.startImport();
    await until(imports, (v) => v.phase === 'finished');
    expect(slept.filter((ms) => ms === 120)).toHaveLength(2);
    expect(slept.filter((ms) => ms === 2000).length).toBeGreaterThanOrEqual(1);
  });

  it('refuses a second start while one runs, and a start with no file', async () => {
    const sales = new FakeSales();
    const gate = deferred<void>();
    sales.gates.set(0, gate.promise);
    const { imports } = await rig('ADMIN', sales.respond);
    expect(await imports.startImport()).toMatchObject({ ok: false, error: { code: 'no_file' } });
    await imports.chooseFile();
    await imports.startImport();
    expect(await imports.startImport()).toMatchObject({ ok: false, error: { code: 'import_running' } });
    expect(await imports.chooseFile()).toMatchObject({ ok: false, error: { code: 'import_running' } });
    gate.resolve();
    await until(imports, (v) => v.phase === 'finished');
  });

  it('shows import_in_progress as returned and keeps the file for another try', async () => {
    const sales = new FakeSales();
    sales.interfere = (r) =>
      path(r) === 'POST /v1/sale-imports' && sales.total === 0
        ? { status: 409, body: example('sales', 'import-in-progress') }
        : undefined;
    const { imports } = await rig('ADMIN', sales.respond);
    await imports.chooseFile();
    expect(await imports.startImport()).toMatchObject({
      ok: false,
      error: { code: 'import_in_progress', correlation_id: 'corr-import-in-progress' },
    });
    expect(await imports.importProgress()).toEqual({ ok: true, value: null });
    sales.interfere = () => undefined;
    expect(await imports.startImport()).toMatchObject({ ok: true });
    await until(imports, (v) => v.phase === 'finished');
  });

  it('retries a create that got no answer with the same Idempotency-Key and body', async () => {
    const sales = new FakeSales();
    let first = true;
    sales.interfere = (r) => {
      if (path(r) !== 'POST /v1/sale-imports' || !first) return undefined;
      first = false;
      return { status: 503, body: errorXml('database_unavailable') };
    };
    const { imports, seen, slept } = await rig('ADMIN', sales.respond, salesCsv(10));
    await imports.chooseFile();
    expect(await imports.startImport()).toMatchObject({ ok: true });
    const creates = seen.filter((s) => path(s) === 'POST /v1/sale-imports');
    expect(creates).toHaveLength(2);
    expect(creates[1]?.headers['Idempotency-Key']).toBe(creates[0]?.headers['Idempotency-Key']);
    expect(creates[1]?.body).toBe(creates[0]?.body);
    expect(slept[0]).toBe(1000);
    await until(imports, (v) => v.phase === 'finished');
  });

  it('takes the 200 of a replayed create as the import it created, and still sends from chunk 0', async () => {
    const sales = new FakeSales();
    sales.total = 9;
    sales.chunkCount = 1;
    sales.interfere = (r) =>
      path(r) === 'POST /v1/sale-imports'
        ? {
            status: 200,
            body: example('sales', 'create-sale-import-replay'),
            headers: { 'Idempotent-Replayed': 'true' },
          }
        : undefined;
    const { imports, seen } = await rig('ADMIN', sales.respond, salesCsv(9));
    await imports.chooseFile();
    const res = await imports.startImport();
    expect(res).toMatchObject({ ok: true, value: { import: { import_id: 17, next_sequence: 1 } } });
    sales.interfere = () => undefined;
    await until(imports, (v) => v.phase === 'finished');
    expect(chunkPuts(seen).map((s) => s.url.split('/').at(-1))).toEqual(['0']);
  });
});

describe('failures while sending', () => {
  it('tries a chunk again after a 503 and after a 429 with Retry-After, sending the same body', async () => {
    const sales = new FakeSales();
    const answers: Reply[] = [
      { status: 503, body: errorXml('database_unavailable') },
      { status: 429, body: errorXml('rate_limited'), headers: { 'Retry-After': '2' } },
    ];
    sales.interfere = (r) => (path(r).endsWith('/chunks/1') ? answers.shift() : undefined);
    const { imports, seen, slept } = await rig('ADMIN', sales.respond);
    await imports.chooseFile();
    await imports.startImport();
    const end = await until(imports, (v) => v.phase === 'finished');
    expect(end.import?.status).toBe('COMPLETED');
    const second = chunkPuts(seen).filter((s) => s.url.endsWith('/chunks/1'));
    expect(second).toHaveLength(3);
    expect(new Set(second.map((s) => s.body)).size).toBe(1);
    expect(slept).toContain(1000);
    expect(slept).toContain(2000);
  });

  it('pauses after three attempts, keeps the file, and resumes at the chunk the service expects', async () => {
    const sales = new FakeSales();
    let down = true;
    sales.interfere = (r) =>
      down && path(r).endsWith('/chunks/1')
        ? { status: 503, body: errorXml('database_unavailable') }
        : undefined;
    const { imports, seen, slept } = await rig('ADMIN', sales.respond);
    await imports.chooseFile();
    await imports.startImport();
    const paused = await until(imports, (v) => v.phase === 'paused');
    expect(paused).toMatchObject({
      can_resume: true,
      chunks_staged: 1,
      error: { code: 'database_unavailable' },
    });
    expect(slept).toEqual(expect.arrayContaining([1000, 3000]));
    expect(chunkPuts(seen).filter((s) => s.url.endsWith('/chunks/1'))).toHaveLength(3);

    down = false;
    seen.length = 0;
    expect(await imports.resumeImport(17)).toMatchObject({ ok: true });
    const end = await until(imports, (v) => v.phase === 'finished');
    expect(end.import?.status).toBe('COMPLETED');
    // GET, then chunk 0 again (a replay: the same file), then chunks 1 and 2.
    expect(sent(seen).slice(0, 4)).toEqual([
      'GET /v1/sale-imports/17',
      'PUT /v1/sale-imports/17/chunks/0',
      'PUT /v1/sale-imports/17/chunks/1',
      'PUT /v1/sale-imports/17/chunks/2',
    ]);
  });

  it('pauses when the service cannot be reached, and says so', async () => {
    const sales = new FakeSales();
    let reachable = true;
    const base = sales.respond;
    const { imports } = await rig('ADMIN', async (r) => {
      if (!reachable && path(r).endsWith('/chunks/1')) throw new TypeError('fetch failed');
      return base(r);
    });
    await imports.chooseFile();
    await imports.startImport();
    reachable = false;
    const paused = await until(imports, (v) => v.phase === 'paused');
    expect(paused.error?.code).toBe('network_unreachable');
    expect(paused.can_resume).toBe(true);
  });

  it('stops for good on chunk_conflict and offers no resume', async () => {
    const sales = new FakeSales();
    sales.interfere = (r) =>
      path(r).endsWith('/chunks/1') ? { status: 409, body: example('sales', 'chunk-conflict') } : undefined;
    const { imports } = await rig('ADMIN', sales.respond);
    await imports.chooseFile();
    await imports.startImport();
    const stopped = await until(imports, (v) => v.phase === 'failed');
    expect(stopped).toMatchObject({ can_resume: false, error: { code: 'chunk_conflict' } });
  });

  it('cancels the import itself on row_count_mismatch, which is final', async () => {
    const sales = new FakeSales();
    sales.interfere = (r) =>
      path(r).endsWith('/chunks/2')
        ? { status: 422, body: example('sales', 'row-count-mismatch') }
        : undefined;
    const { imports, seen } = await rig('ADMIN', sales.respond);
    await imports.chooseFile();
    await imports.startImport();
    const stopped = await until(imports, (v) => v.phase === 'failed');
    expect(stopped.error).toMatchObject({
      code: 'row_count_mismatch',
      correlation_id: 'corr-row-count-mismatch',
    });
    expect(stopped.import?.status).toBe('CANCELLED');
    const cancel = seen.find((s) => path(s) === 'PUT /v1/sale-imports/17/status');
    expect(cancel?.body).toContain('<status>CANCELLED</status>');
    expect(stopped.can_resume).toBe(false);
  });
});

describe('resuming', () => {
  const original = salesCsv(320);

  /** An import that already staged chunk 0 of the original file. */
  function staged(): FakeSales {
    const sales = new FakeSales();
    const plan = planImport(NS, Buffer.from(original));
    sales.total = 320;
    sales.chunkCount = 3;
    sales.staged = 1;
    sales.received = 150;
    sales.pending = 150;
    sales.bodies[0] = chunkXml(NS, plan.chunks[0]?.rows ?? []);
    return sales;
  }

  it('continues a staged import from a fresh start of the app, after sending chunk 0 again as a replay', async () => {
    const sales = staged();
    const { imports, seen } = await rig('ADMIN', sales.respond, original);
    await imports.chooseFile();
    expect(await imports.resumeImport(17)).toMatchObject({ ok: true, value: { chunks_staged: 1 } });
    const end = await until(imports, (v) => v.phase === 'finished');
    expect(end.import?.status).toBe('COMPLETED');
    expect(chunkPuts(seen).map((s) => s.url.split('/').at(-1))).toEqual(['0', '1', '2']);
  });

  it('refuses a file whose rows differ from the staged chunk, through chunk_conflict', async () => {
    const sales = staged();
    const other = salesCsv(320, (line, cells) => (line === 5 ? [...cells.slice(0, 7), '99.90'] : cells));
    const { imports, seen } = await rig('ADMIN', sales.respond, other);
    await imports.chooseFile();
    expect(await imports.resumeImport(17)).toMatchObject({ ok: false, error: { code: 'different_file' } });
    expect(sent(seen)).toEqual(['GET /v1/sale-imports/17', 'PUT /v1/sale-imports/17/chunks/0']);
    expect(await imports.importProgress()).toEqual({ ok: true, value: null });
  });

  it('refuses a file with another number of rows or chunks without sending a chunk', async () => {
    const sales = staged();
    const { imports, seen } = await rig('ADMIN', sales.respond, salesCsv(319));
    await imports.chooseFile();
    expect(await imports.resumeImport(17)).toMatchObject({ ok: false, error: { code: 'different_file' } });
    expect(sent(seen)).toEqual(['GET /v1/sale-imports/17']);
  });

  it('refuses an import that is no longer receiving', async () => {
    const sales = staged();
    sales.status = 'COMPLETED';
    const { imports } = await rig('ADMIN', sales.respond, original);
    await imports.chooseFile();
    expect(await imports.resumeImport(17)).toMatchObject({
      ok: false,
      error: { code: 'import_not_resumable' },
    });
  });

  it('needs the file chosen again', async () => {
    const { imports } = await rig('ADMIN', staged().respond, original);
    expect(await imports.resumeImport(17)).toMatchObject({ ok: false, error: { code: 'no_file' } });
  });
});

describe('cancelling', () => {
  it('stops sending, asks the service to cancel and keeps what it counted', async () => {
    const sales = new FakeSales();
    const gate = deferred<void>();
    sales.gates.set(1, gate.promise);
    const { imports, seen } = await rig('ADMIN', sales.respond);
    await imports.chooseFile();
    await imports.startImport();
    await until(imports, (v) => v.chunks_staged === 1);
    const cancelled = await imports.cancelImport(17);
    expect(cancelled).toMatchObject({ ok: true, value: { status: 'CANCELLED' } });
    gate.resolve();
    const end = await until(imports, (v) => v.phase === 'finished');
    expect(end.import?.status).toBe('CANCELLED');
    await new Promise((resolve) => setTimeout(resolve, 30));
    // Chunk 2 was never sent.
    expect(chunkPuts(seen).map((s) => s.url.split('/').at(-1))).toEqual(['0', '1']);
    expect(seen.filter((s) => path(s) === 'PUT /v1/sale-imports/17/status')).toHaveLength(1);
  });

  it('cancels an import listed from an earlier session, which this console is not running', async () => {
    const sales = new FakeSales();
    sales.total = 9;
    sales.chunkCount = 2;
    const { imports } = await rig('ADMIN', sales.respond);
    expect(await imports.cancelImport(17)).toMatchObject({
      ok: true,
      value: { import_id: 17, status: 'CANCELLED' },
    });
  });

  it('shows import_finished as returned', async () => {
    const { imports } = await rig('ADMIN', () => ({
      status: 409,
      body: example('sales', 'import-finished'),
    }));
    expect(await imports.cancelImport(17)).toMatchObject({ ok: false, error: { code: 'import_finished' } });
  });
});

describe('reading', () => {
  const read: Responder = (r) => {
    const p = path(r);
    if (p.startsWith('GET /v1/sale-imports/17/rejections'))
      return { status: 200, body: example('sales', 'list-sale-import-rejections') };
    if (p.startsWith('GET /v1/sale-imports/17'))
      return { status: 200, body: example('sales', 'get-sale-import-completed') };
    if (p.startsWith('GET /v1/sale-imports'))
      return { status: 200, body: example('sales', 'list-sale-imports') };
    return { status: 404, body: example('sales', 'sale-import-not-found') };
  };

  it('lists the recent imports, newest first', async () => {
    const { imports, seen } = await rig('ADMIN', read);
    const res = await imports.listImports({ page: 1, size: 10 });
    expect(sent(seen)).toEqual(['GET /v1/sale-imports']);
    expect(new URL(seen[0]?.url as string).search).toBe('?page=1&size=10');
    expect(res.ok && res.value.items.map((i) => [i.import_id, i.status])).toEqual([
      [18, 'CANCELLED'],
      [17, 'COMPLETED'],
      [16, 'EXPIRED'],
    ]);
    expect(res.ok && res.value.items[1]).toMatchObject({ received: 9, accepted: 5, rejected: 4, pending: 0 });
  });

  it('reads one import with its reconciled counts', async () => {
    const { imports } = await rig('ADMIN', read);
    const res = await imports.getImport(17);
    expect(res).toMatchObject({
      ok: true,
      value: { import_id: 17, status: 'COMPLETED', finished_at: '2026-10-28T15:02:14Z', next_sequence: 2 },
    });
  });

  it('reads the report with line, field and reason, and tells a repeat in the file from a recorded sale', async () => {
    const { imports, seen } = await rig('ADMIN', read);
    const res = await imports.listRejections({ import_id: 17, page: 1, size: 100 });
    expect(new URL(seen[0]?.url as string).search).toBe('?page=1&size=100');
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.value.page).toEqual({ page: 1, size: 20, total: 4 });
    const [qty, future, repeat, recorded] = res.value.items;
    expect(qty).toEqual({
      line: 4,
      field: 'quantity',
      code: 'invalid_quantity',
      message: 'Line 4: quantity must be a positive whole number; the cell holds "0".',
    });
    expect(future).toMatchObject({ line: 6, field: 'occurred_at', code: 'future_dated' });
    expect(repeat).toEqual({
      line: 8,
      code: 'duplicate_row',
      message: expect.stringContaining('repeats line 3 of this file'),
      duplicate_of: 'FILE',
      first_line: 3,
    });
    expect(recorded).toEqual({
      line: 9,
      code: 'duplicate_row',
      message: expect.stringContaining('already recorded as sale 4790'),
      duplicate_of: 'RECORDED_SALE',
      sale_id: 4790,
    });
  });

  it('shows the file text the service quoted, not the XML that carried it', async () => {
    const body = example('sales', 'list-sale-import-rejections').replace(
      'the cell holds "0".',
      'the cell holds &quot;A&amp;B &lt;x&gt; &#65;&amp;lt;&quot;.',
    );
    const { imports } = await rig('ADMIN', () => ({ status: 200, body }));
    const res = await imports.listRejections({ import_id: 17 });
    expect(res.ok && res.value.items[0]?.message).toBe(
      'Line 4: quantity must be a positive whole number; the cell holds "A&B <x> A&lt;".',
    );
    expect(xmlText('&#x1F600; &#0; &#xD800; &bogus;')).toBe('\u{1F600} &#0; &#xD800; &bogus;');
  });

  it('shows an unknown import as returned and an empty report as an empty page', async () => {
    const { imports } = await rig('ADMIN', (r) =>
      path(r).includes('/rejections')
        ? {
            status: 200,
            body: example('sales', 'list-sale-import-rejections')
              .replace(/<SaleImportRejection>[\s\S]*<\/SaleImportRejection>/, '')
              .replace('<common:total>4', '<common:total>0'),
          }
        : { status: 404, body: example('sales', 'sale-import-not-found') },
    );
    expect(await imports.getImport(999999)).toMatchObject({
      ok: false,
      error: { code: 'sale_import_not_found' },
    });
    const empty = await imports.listRejections({ import_id: 17 });
    expect(empty).toMatchObject({ ok: true, value: { items: [], page: { total: 0 } } });
  });

  it('refuses a malformed query before sending it', async () => {
    const { imports, seen } = await rig('ADMIN', read);
    for (const q of [
      {},
      { import_id: 0 },
      { import_id: '17' },
      { import_id: 17, page: 0 },
      { import_id: 17, size: 101 },
      'x',
    ])
      expect(await imports.listRejections(q)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    for (const id of [0, -1, 1.5, '17', '17/../1', Number.MAX_SAFE_INTEGER + 1]) {
      expect(await imports.getImport(id)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
      expect(await imports.cancelImport(id)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
      expect(await imports.resumeImport(id)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    }
    expect(await imports.listImports({ size: 101 })).toMatchObject({
      ok: false,
      error: { code: 'invalid_input' },
    });
    expect(seen).toEqual([]);
  });
});
