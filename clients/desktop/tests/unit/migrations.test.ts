import { describe, expect, it } from 'vitest';
import { Migrations } from '../../src/main/migrations';
import type { MigrationMatrixView } from '../../src/shared/api';
import { MIGRATION_OPERATIONS, visibleGroups } from '../../src/shared/modules';
import {
  buildGrid,
  describeMismatch,
  methodologicalText,
  reconcile,
  responsibleText,
  stateName,
  versionsText,
} from '../../src/shared/migration-matrix';
import { claims, example, makeOffice, MemoryVault, tokenResult, type Responder } from '../support/fake';
import { explanationXml, kindOf, matrixXml, membersXml, MOVER, type Kind } from '../support/segments';

const SEG = '/api/segments';
const WITHIN = { from: '2026-09-14T03:00:00Z', to: '2026-10-12T03:00:00Z' };
const ACROSS = { from: '2026-10-12T03:00:00Z', to: '2026-10-21T03:00:00Z' };

function responder(
  role: string,
  override?: (url: URL) => { status: number; body: string } | undefined,
): Responder {
  return (r) => {
    const url = new URL(r.url);
    if (r.method === 'POST' && url.pathname.endsWith('/v1/sessions'))
      return { status: 201, body: tokenResult('a1', 'r1') };
    if (url.pathname.endsWith('/v1/sessions/current'))
      return r.method === 'GET' ? { status: 200, body: claims(role) } : { status: 204 };
    const refusal = override?.(url);
    if (refusal !== undefined) return refusal;
    const kind: Kind = kindOf(url.searchParams.get('to') ?? '');
    if (url.pathname === `${SEG}/v1/segment-migrations/matrix`) return { status: 200, body: matrixXml(kind) };
    if (url.pathname === `${SEG}/v1/segment-migrations`)
      return { status: 200, body: membersXml(kind, url.searchParams) };
    if (url.pathname === `${SEG}/v1/segment-migrations/explanation`)
      return { status: 200, body: explanationXml(kind, url.searchParams.get('customer_id') ?? '') };
    return { status: 500, body: '' };
  };
}

async function setup(role: string, override?: Parameters<typeof responder>[1]) {
  const { office, seen } = makeOffice(
    responder(role, override),
    new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') }),
  );
  await office.signIn({ email: 'a@b.c', password: 'pw' });
  return { migrations: new Migrations(office), seen, office };
}

const calls = (seen: { url: string }[]): string[] =>
  seen.filter((s) => s.url.includes('/segment-migrations')).map((s) => s.url);

async function matrixOf(window: { from: string; to: string }): Promise<MigrationMatrixView> {
  const { migrations } = await setup('ANALYST');
  const res = await migrations.matrix(window);
  if (!res.ok) throw new Error(res.error.code);
  return res.value;
}

describe('who sees the module', () => {
  it('shows the module only to a role the matrix operation allows', async () => {
    const analyst = await setup('ANALYST');
    const planner = await setup('INVENTORY_PLANNER');
    const names = (state: { status: string; operations?: string[] }) =>
      visibleGroups(state.operations ?? []).flatMap((g) => g.modules.map((m) => m.name));
    expect(names(analyst.office.state() as never)).toContain('migrations');
    expect(names(planner.office.state() as never)).not.toContain('migrations');
    // The module's three reads are the three operations of the contract, with the same roles.
    for (const operation of Object.values(MIGRATION_OPERATIONS))
      expect(analyst.office.state()).toMatchObject({ operations: expect.arrayContaining([operation]) });
  });

  it('reads nothing for a role the contract does not allow, whatever the screen sends', async () => {
    const { migrations, seen } = await setup('INVENTORY_PLANNER');
    const before = seen.length;
    expect(await migrations.matrix(WITHIN)).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(await migrations.list({ ...WITHIN, direction: 'entries', label_code: 'CHAMPION' })).toMatchObject({
      ok: false,
      error: { code: 'forbidden_role' },
    });
    expect(await migrations.explain({ ...WITHIN, customer_id: MOVER.within.id })).toMatchObject({
      ok: false,
      error: { code: 'forbidden_role' },
    });
    expect(seen.length).toBe(before);
  });
});

describe('the matrix between two periods', () => {
  it('reads the recorded matrix through the XSD and asks for the two instants in XML', async () => {
    const { migrations, seen } = await setup('ANALYST');
    const res = await migrations.matrix(WITHIN);
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value).toMatchObject({
      from: WITHIN.from,
      to: WITHIN.to,
      from_model_version_id: 3,
      to_model_version_id: 3,
      methodological: false,
      customer_count: 49,
    });
    expect(res.value.totals).toHaveLength(6);
    expect(res.value.cells).toHaveLength(6);
    const asked = seen.find((s) => s.url.includes('/matrix'));
    expect(asked?.headers.Accept).toBe('application/xml');
    const params = new URL(asked?.url ?? '').searchParams;
    expect([params.get('from'), params.get('to')]).toEqual([WITHIN.from, WITHIN.to]);
  });

  it('keeps the earlier instant as the before state whatever order the dates were chosen in (RN-59)', async () => {
    const { migrations, seen } = await setup('ANALYST');
    await migrations.matrix({ from: WITHIN.to, to: WITHIN.from });
    const params = new URL(seen.find((s) => s.url.includes('/matrix'))?.url ?? '').searchParams;
    expect([params.get('from'), params.get('to')]).toEqual([WITHIN.from, WITHIN.to]);
  });

  it('refuses dates the contract cannot compare before anything is sent', async () => {
    const { migrations, seen } = await setup('ANALYST');
    const before = seen.length;
    for (const bad of [
      { from: WITHIN.from, to: WITHIN.from },
      { from: 'yesterday', to: WITHIN.to },
      { from: WITHIN.from },
      { from: '', to: WITHIN.to },
      undefined,
    ])
      expect(await migrations.matrix(bad)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(seen.length).toBe(before);
  });

  it('shows the service’s own refusal as returned', async () => {
    const { migrations } = await setup('ANALYST', (url) =>
      url.pathname.endsWith('/matrix')
        ? { status: 422, body: example('segments', 'invalid-migration-window') }
        : undefined,
    );
    const res = await migrations.matrix(WITHIN);
    expect(res).toMatchObject({
      ok: false,
      error: { code: 'invalid_query', details: [{ field: 'from', code: 'not_before_to' }] },
    });
  });

  it('rejects a matrix that breaks its contract and passes none of it on', async () => {
    const { migrations } = await setup('ANALYST', (url) =>
      url.pathname.endsWith('/matrix')
        ? { status: 200, body: example('segments', 'migration-matrix-without-methodological.invalid') }
        : undefined,
    );
    expect(await migrations.matrix(WITHIN)).toMatchObject({ ok: false, error: { code: 'contract_defect' } });
  });
});

describe('totals reconcile', () => {
  for (const [name, window] of [
    ['within one version', WITHIN],
    ['across two versions', ACROSS],
  ] as const)
    it(`adds up row by row and column by column, ${name}`, async () => {
      const matrix = await matrixOf(window);
      const grid = buildGrid(matrix, null);
      // Every state's row of cells is its before count and its column its after count.
      for (const t of matrix.totals) {
        const key = t.state === 'label' ? `label:${t.label_code}` : t.state;
        expect(grid.rowTotals.get(key) ?? 0, `${key} before`).toBe(t.before_count);
        expect(grid.columnTotals.get(key) ?? 0, `${key} after`).toBe(t.after_count);
      }
      // And the three grand totals are one number: the customers compared.
      expect(grid.grandTotal).toBe(matrix.customer_count);
      expect(matrix.totals.reduce((n, t) => n + t.before_count, 0)).toBe(matrix.customer_count);
      expect(matrix.totals.reduce((n, t) => n + t.after_count, 0)).toBe(matrix.customer_count);
      expect(reconcile(matrix)).toEqual({ ok: true, mismatches: [] });
    });

  it('finds the customers who entered and left each state in the cells', async () => {
    const matrix = await matrixOf(WITHIN);
    const champion = matrix.totals.find((t) => t.label_code === 'CHAMPION');
    // 18 hold it after, 13 of them held it before: 5 entered; 16 held it before, 3 left.
    expect([champion?.after_count, champion?.entries, champion?.before_count, champion?.exits]).toEqual([
      18, 5, 16, 3,
    ]);
  });

  it('says so when a cell, a total or the customer count has been altered', async () => {
    const matrix = await matrixOf(WITHIN);
    const names = (key: string): string => key;
    const altered = (change: (m: MigrationMatrixView) => void): MigrationMatrixView => {
      const copy = structuredClone(matrix);
      change(copy);
      return copy;
    };
    const cell = altered((m) => {
      (m.cells[0] as { customer_count: number }).customer_count += 1;
    });
    const result = reconcile(cell);
    expect(result.ok).toBe(false);
    expect(result.mismatches.map((m) => m.kind)).toEqual(
      expect.arrayContaining(['before', 'after', 'customers']),
    );
    expect(result.mismatches.map((m) => describeMismatch(m, names)).join(' ')).toContain('label:CHAMPION');

    const count = altered((m) => {
      m.customer_count = 50;
    });
    expect(reconcile(count).mismatches.map((m) => m.kind)).toEqual(
      expect.arrayContaining(['customers', 'before_sum', 'after_sum']),
    );

    const entries = altered((m) => {
      (m.totals[0] as { entries: number }).entries += 1;
    });
    expect(reconcile(entries).mismatches).toEqual([
      { kind: 'entries', key: 'label:CHAMPION', stated: 6, computed: 5 },
    ]);

    const unlisted = altered((m) => {
      m.totals = m.totals.filter((t) => t.label_code !== 'POTENTIAL');
    });
    expect(reconcile(unlisted).mismatches).toContainEqual({ kind: 'unlisted', key: 'label:POTENTIAL' });
  });

  it('draws the full grid from the vocabulary and names the states as RN-59 does', async () => {
    const matrix = await matrixOf(WITHIN);
    const vocabulary = [
      { label_code: 'LOYAL', name: 'Loyal', ordinal_position: 2, size: 0 },
      { label_code: 'CHAMPION', name: 'Champion', ordinal_position: 1, size: 0 },
      { label_code: 'LOST', name: 'Lost', ordinal_position: 6, size: 0 },
    ];
    const grid = buildGrid(matrix, vocabulary);
    // Business order first, then a label the vocabulary lacks, then Unassigned and New.
    expect(grid.rows.map((a) => a.key)).toEqual([
      'label:CHAMPION',
      'label:LOYAL',
      'label:LOST',
      'label:POTENTIAL',
      'label:AT_RISK',
      'unassigned',
      'new',
    ]);
    expect(grid.rows.map((a) => a.name)).toEqual([
      'Champion',
      'Loyal',
      'Lost',
      'POTENTIAL',
      'AT_RISK',
      'Unassigned',
      'New',
    ]);
    expect(grid.columns.at(-1)?.name).toBe('Not in later run');
    // A pair the service left out is a zero cell, still on the grid.
    expect(grid.cells.has('label:LOST>label:CHAMPION')).toBe(false);
    expect(stateName({ state: 'new' }, 'before', new Map())).toBe('New');
    expect(stateName({ state: 'unassigned' }, 'after', new Map())).toBe('Unassigned');
  });
});

describe('each cell opens its customers', () => {
  it('lists, for every cell of the matrix, as many customers as the cell counted', async () => {
    const matrix = await matrixOf(WITHIN);
    const { migrations, seen } = await setup('ANALYST');
    for (const cell of matrix.cells) {
      let listed = 0;
      for (let page = 1; ; page += 1) {
        const res = await migrations.list({
          ...WITHIN,
          previous_state: cell.previous_state,
          ...(cell.previous_label_code === undefined
            ? {}
            : { previous_label_code: cell.previous_label_code }),
          new_state: cell.new_state,
          ...(cell.new_label_code === undefined ? {} : { new_label_code: cell.new_label_code }),
          page,
          size: 10,
        });
        if (!res.ok) throw new Error(res.error.code);
        // Every customer carries the very pair of the cell it was opened from, and its movement.
        for (const m of res.value.items)
          expect([
            m.previous_state,
            m.previous_label_code,
            m.new_state,
            m.new_label_code,
            m.movement,
          ]).toEqual([
            cell.previous_state,
            cell.previous_label_code,
            cell.new_state,
            cell.new_label_code,
            cell.movement,
          ]);
        expect(res.value.page.total).toBe(cell.customer_count);
        listed += res.value.items.length;
        if (listed >= res.value.page.total || res.value.items.length === 0) break;
      }
      expect(
        listed,
        `${cell.previous_label_code ?? cell.previous_state}>${cell.new_label_code ?? cell.new_state}`,
      ).toBe(cell.customer_count);
    }
    const first = new URL(calls(seen)[0] ?? '').searchParams;
    expect(first.get('previous_state')).toBe('label');
    expect(first.get('previous_label_code')).toBe('CHAMPION');
  });

  it('lists a label’s entries and exits, as many as the matrix counted for it', async () => {
    const matrix = await matrixOf(WITHIN);
    const { migrations } = await setup('ANALYST');
    for (const t of matrix.totals.filter((x) => x.state === 'label'))
      for (const direction of ['entries', 'exits'] as const) {
        const res = await migrations.list({ ...WITHIN, direction, label_code: t.label_code });
        if (!res.ok) throw new Error(res.error.code);
        expect(res.value.page.total, `${t.label_code} ${direction}`).toBe(t[direction]);
      }
  });

  it('reads the recorded page, with its responsible variables and its kind', async () => {
    const { migrations } = await setup('ANALYST', (url) =>
      url.pathname.endsWith('/segment-migrations')
        ? { status: 200, body: example('segments', 'migrations-cell-members') }
        : undefined,
    );
    const res = await migrations.list({
      ...WITHIN,
      previous_state: 'label',
      previous_label_code: 'LOYAL',
      new_state: 'label',
      new_label_code: 'CHAMPION',
    });
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.items.map((m) => m.responsible_variables)).toEqual([['recency', 'monetary'], []]);
    expect(res.value.items.map((m) => m.rank_only)).toEqual([false, true]);
    expect(res.value.items.map((m) => m.methodological)).toEqual([false, false]);
    expect(responsibleText(res.value.items[0]!)).toBe('Recency, Monetary');
    expect(responsibleText(res.value.items[1]!)).toBe('None: only the rank moved');
  });

  it('can keep the behaviour and leave out what a model change may explain', async () => {
    const { migrations, seen } = await setup('ANALYST');
    const across = await migrations.list({
      ...ACROSS,
      direction: 'entries',
      label_code: 'LOYAL',
      methodological: false,
    });
    expect(across).toMatchObject({ ok: true, value: { page: { total: 0 } } });
    expect(new URL(calls(seen).at(-1) ?? '').searchParams.get('methodological')).toBe('false');
  });

  it('refuses a query the service would refuse, before anything is sent', async () => {
    const { migrations, seen } = await setup('ANALYST');
    const before = seen.length;
    const cell = { previous_state: 'label', previous_label_code: 'LOYAL', new_state: 'unassigned' };
    for (const bad of [
      { ...WITHIN },
      { ...WITHIN, ...cell, direction: 'entries', label_code: 'LOYAL' },
      { ...WITHIN, direction: 'entries' },
      { ...WITHIN, direction: 'sideways', label_code: 'LOYAL' },
      { ...WITHIN, previous_state: 'label', new_state: 'unassigned' },
      { ...WITHIN, previous_state: 'new', previous_label_code: 'LOYAL', new_state: 'unassigned' },
      { ...WITHIN, ...cell, new_state: 'retired' },
      { ...WITHIN, ...cell, methodological: 'no' },
      { ...WITHIN, ...cell, page: 0 },
      { ...WITHIN, ...cell, size: 101 },
      { ...WITHIN, ...cell, new_state: 'label', new_label_code: 'x'.repeat(65) },
    ])
      expect(await migrations.list(bad), JSON.stringify(bad)).toMatchObject({
        ok: false,
        error: { code: expect.stringMatching(/^invalid_/) },
      });
    expect(seen.length).toBe(before);
  });

  it('sends a label code only with a label state, and builds the query from the checked values', async () => {
    const { migrations, seen } = await setup('ANALYST');
    await migrations.list({ ...WITHIN, previous_state: 'new', new_state: 'label', new_label_code: 'LOYAL' });
    const q = new URL(calls(seen).at(-1) ?? '').searchParams;
    expect([...q.keys()].sort()).toEqual(
      ['from', 'new_label_code', 'new_state', 'page', 'previous_state', 'size', 'to'].sort(),
    );
  });
});

describe('two versions are marked methodological', () => {
  it('marks the matrix across versions, and says which versions', async () => {
    const matrix = await matrixOf(ACROSS);
    expect(matrix).toMatchObject({
      methodological: true,
      from_model_version_id: 3,
      to_model_version_id: 4,
    });
    expect(versionsText(matrix)).toBe('Before: model version 3. After: model version 4.');
    expect(methodologicalText(matrix)).toMatch(/different model versions.*not shown as behaviour/);
  });

  it('does not mark a matrix within one version', async () => {
    const matrix = await matrixOf(WITHIN);
    expect(matrix.methodological).toBe(false);
    expect(methodologicalText(matrix)).toMatch(/shows customer behaviour/);
  });

  it('marks a matrix whose version is not known yet, which is methodological too', async () => {
    const matrix = await matrixOf(WITHIN);
    const pending: MigrationMatrixView = { ...matrix, methodological: true };
    delete pending.to_model_version_id;
    expect(versionsText(pending)).toBe('Before: model version 3. After: unknown.');
    expect(methodologicalText(pending)).toMatch(/not known/);
  });

  it('carries the mark onto every customer of a cross-version cell and never counts it as behaviour', async () => {
    const { migrations } = await setup('ANALYST');
    const res = await migrations.list({ ...ACROSS, direction: 'entries', label_code: 'LOYAL' });
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.items.length).toBeGreaterThan(0);
    expect(res.value.items.every((m) => m.methodological)).toBe(true);
    expect(res.value.items.every((m) => m.cause === 'VERSION_CHANGE')).toBe(true);
  });
});

describe('a move names its responsible variables', () => {
  it('reads the explanation and agrees the responsible variables with the judged measures', async () => {
    const { migrations, seen } = await setup('ANALYST');
    const res = await migrations.explain({ ...WITHIN, customer_id: MOVER.within.id });
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.measures.map((m) => [m.variable, m.judgement])).toEqual([
      ['recency', 'changed'],
      ['frequency', 'stable'],
      ['monetary', 'changed'],
    ]);
    // The variables the migration names are the ones judged changed, in the same words.
    expect(res.value.migration.responsible_variables).toEqual(
      res.value.measures.filter((m) => m.judgement === 'changed').map((m) => m.variable),
    );
    expect(res.value.measures[0]).toMatchObject({ previous_value: '41', new_value: '9', unit: 'days' });
    expect(res.value.measures[2]).toMatchObject({
      previous_value: '1200.00',
      new_value: '1560.00',
      unit: 'MXN',
    });
    expect(res.value.measures[0]?.statement).toMatch(/^Recency changed/);
    const q = new URL(seen.at(-1)?.url ?? '').searchParams;
    expect([q.get('customer_id'), q.get('from'), q.get('to')]).toEqual([
      MOVER.within.id,
      WITHIN.from,
      WITHIN.to,
    ]);
  });

  it('says that only the rank moved when the label changed and no variable did', async () => {
    const { migrations } = await setup('ANALYST');
    const res = await migrations.explain({ ...ACROSS, customer_id: MOVER.across.id });
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.migration).toMatchObject({
      rank_only: true,
      methodological: true,
      responsible_variables: [],
    });
    expect(res.value.measures.find((m) => m.variable === 'recency')).toMatchObject({
      judgement: 'stable',
      rank_changed: true,
    });
  });

  it('reports a new customer as not compared, with the reason', async () => {
    const { migrations } = await setup('ANALYST');
    const res = await migrations.explain({ ...ACROSS, customer_id: '0c9f6a3e-5d2b-4c8e-9a41-7e3b2d1f8a66' });
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.measures.map((m) => [m.judgement, m.not_compared_reason, m.previous_value])).toEqual([
      ['not_compared', 'new_customer', undefined],
      ['not_compared', 'new_customer', undefined],
      ['not_compared', 'new_customer', undefined],
    ]);
    expect(responsibleText(res.value.migration)).toBe('None to compare');
  });

  it('refuses a customer that is not an id before anything is sent', async () => {
    const { migrations, seen } = await setup('ANALYST');
    const before = seen.length;
    for (const bad of ['', '1 OR 1=1', 7, undefined])
      expect(await migrations.explain({ ...WITHIN, customer_id: bad })).toMatchObject({
        ok: false,
        error: { code: 'invalid_input' },
      });
    expect(seen.length).toBe(before);
  });

  it('shows an unknown customer as the service refused it', async () => {
    const { migrations } = await setup('ANALYST', (url) =>
      url.pathname.endsWith('/explanation')
        ? { status: 404, body: example('segments', 'customer-not-found') }
        : undefined,
    );
    expect(await migrations.explain({ ...WITHIN, customer_id: MOVER.within.id })).toMatchObject({
      ok: false,
      error: { code: 'customer_not_found' },
    });
  });
});
