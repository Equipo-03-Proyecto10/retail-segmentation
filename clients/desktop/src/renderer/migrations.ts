import type {
  MigrationExplanationView,
  MigrationListQuery,
  MigrationListView,
  MigrationMatrixView,
  MigrationView,
  SegmentLabelView,
} from '../shared/api';
import {
  buildGrid,
  describeMismatch,
  labelNames,
  methodologicalText,
  reconcile,
  responsibleText,
  stateKey,
  stateName,
  variableName,
  versionsText,
  type Axis,
  type Grid,
  type StateRef,
} from '../shared/migration-matrix';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, formField, pager, select, testid, textInput } from './dom';
import { latestRequest } from './latest-request';

// The migration matrix (F28-06, #875): two dates, the states the customers held at each, every
// cell opening the customers behind it, and, for one customer, the variables responsible for the
// move. The classification is segments-service's (RN-59, RN-60); the screen draws it, proves that
// its totals add up, and marks a comparison across model versions as methodological (ADR-0064).
// Everything the service sent is set with textContent, never as HTML.

const DAY = 86_400_000;
const PAGE_SIZE = 20;

const METHODOLOGICAL: [string, string][] = [
  ['', 'All moves'],
  ['false', 'Behaviour only'],
  ['true', 'Methodological only'],
];

const REASONS: Record<string, string> = {
  new_customer: 'the customer was new at the earlier date',
  unassigned: 'the run scored the customer and found no purchase to label',
};

const pad = (n: number): string => String(n).padStart(2, '0');

/** A date as the datetime-local field reads it, in UTC. */
function localInput(t: number): string {
  const d = new Date(t);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

/** The field's value as an RFC 3339 instant in UTC; anything else is left for the main process to refuse. */
function instantOf(value: string): string {
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return `${value}:00Z`;
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(value)) return `${value}Z`;
  return value;
}

function facts(rows: [string, string, string][]): HTMLElement {
  const dl = el('dl', { cls: 'compact' });
  for (const [name, value, id] of rows)
    dl.append(el('dt', { text: name }), testid(el('dd', { text: value }), id));
  return dl;
}

function head(columns: string[]): HTMLElement {
  const tr = el('tr');
  for (const c of columns) {
    const th = el('th', { text: c });
    th.scope = 'col';
    tr.append(th);
  }
  return el('thead', {}, tr);
}

const ref = (state: MigrationView['previous_state'], code: string | undefined): StateRef =>
  code === undefined ? { state } : { state, label_code: code };

export function renderMigrations(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const message = el('div');
  message.setAttribute('role', 'status');
  const matrixArea = el('div');
  const membersPanel = testid(el('section', { cls: 'panel' }), 'migration-members');
  const explainPanel = testid(el('section', { cls: 'panel' }), 'migration-explanation');
  const latestMatrix = latestRequest();
  const latestMembers = latestRequest();
  const latestExplanation = latestRequest();

  const today = Math.floor(Date.now() / DAY) * DAY;
  const from = textInput(localInput(today - 28 * DAY), 'datetime-local');
  const to = textInput(localInput(today), 'datetime-local');
  const open = el('button', { text: 'Open matrix' });
  open.type = 'submit';
  const form = testid(
    el(
      'form',
      { cls: 'filters' },
      formField('from', 'Earlier date (UTC)', from, [], 'm'),
      formField('to', 'Later date (UTC)', to, [], 'm'),
      open,
    ),
    'migration-form',
  );
  form.noValidate = true;
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    void openMatrix();
  });
  host.replaceChildren(
    el('h2', { text: 'Migration matrix' }),
    el('p', {
      text: 'Compare the segment each customer held on two dates. The earlier date is always the before state.',
    }),
    form,
    message,
    matrixArea,
    membersPanel,
    explainPanel,
  );

  async function openMatrix(): Promise<void> {
    const entered = { from: instantOf(from.value), to: instantOf(to.value) };
    // The matrix being replaced owns the lists and the explanation below it: drop them and what is in flight.
    latestMembers.begin();
    latestExplanation.begin();
    membersPanel.replaceChildren();
    explainPanel.replaceChildren();
    message.replaceChildren();
    matrixArea.replaceChildren(testid(el('p', { text: 'Loading…' }), 'matrix-loading'));
    await latestMatrix(
      () => Promise.all([api.migrationMatrix(entered), api.segmentVocabulary()]),
      ([matrix, vocabulary]) => {
        if (!matrix.ok) return void matrixArea.replaceChildren(errorBox(matrix.error));
        // A vocabulary that cannot be read leaves the codes as names and the axes as the matrix has them.
        const labels = vocabulary.ok ? vocabulary.value.labels : null;
        if (!vocabulary.ok)
          message.replaceChildren(
            testid(
              el('p', {
                cls: 'notice',
                text: `The label names could not be read (${vocabulary.error.code}); label codes are shown.`,
              }),
              'matrix-names-unavailable',
            ),
          );
        const reversed = Date.parse(entered.from) > Date.parse(entered.to);
        showMatrix(matrix.value, labels, reversed);
      },
    );
  }

  function showMatrix(
    matrix: MigrationMatrixView,
    labels: readonly SegmentLabelView[] | null,
    reversed: boolean,
  ): void {
    const names = labelNames(labels ?? []);
    const grid = buildGrid(matrix, labels);
    const sides = new Map<string, string>([...grid.rows, ...grid.columns].map((a) => [a.key, a.name]));
    const nameOf = (key: string): string => sides.get(key) ?? key;
    const parts: Node[] = [];

    const banner = el('p', {
      cls: matrix.methodological ? 'warning methodological' : 'notice',
      text: matrix.methodological
        ? `Methodological comparison. ${methodologicalText(matrix)}`
        : methodologicalText(matrix),
    });
    banner.setAttribute('role', 'status');
    parts.push(testid(banner, matrix.methodological ? 'matrix-methodological' : 'matrix-behaviour'));

    parts.push(
      facts([
        ['Before (earlier date)', matrix.from, 'matrix-from'],
        ['After (later date)', matrix.to, 'matrix-to'],
        ['Model versions', versionsText(matrix), 'matrix-versions'],
        ['Customers compared', String(matrix.customer_count), 'matrix-customers'],
      ]),
    );
    if (reversed)
      parts.push(
        testid(
          el('p', {
            cls: 'notice',
            text: 'The earlier date is always the before state, so the dates were read in order.',
          }),
          'matrix-reordered',
        ),
      );
    parts.push(el('h3', { text: 'Matrix' }), gridView(matrix, grid, names));
    parts.push(reconciliationView(matrix, nameOf));
    parts.push(el('h3', { text: 'Entries and exits' }), totalsView(matrix, names));
    matrixArea.replaceChildren(...parts);
  }

  function gridView(
    matrix: MigrationMatrixView,
    grid: Grid,
    names: ReadonlyMap<string, string>,
  ): HTMLElement {
    const cornerHead = el('tr');
    const corner = el('th', { text: 'Before \\ After' });
    corner.scope = 'col';
    cornerHead.append(corner);
    for (const c of grid.columns) {
      const th = el('th', { text: c.name });
      th.scope = 'col';
      cornerHead.append(th);
    }
    const totalHead = el('th', { text: 'Row total' });
    totalHead.scope = 'col';
    cornerHead.append(totalHead);

    const body = el('tbody');
    for (const row of grid.rows) {
      const tr = el('tr');
      const rowHead = el('th', { text: row.name });
      rowHead.scope = 'row';
      tr.append(rowHead);
      for (const column of grid.columns) tr.append(cellView(matrix, grid, row, column, names));
      tr.append(
        testid(
          el('td', { cls: 'num', text: String(grid.rowTotals.get(row.key) ?? 0) }),
          `row-total-${row.key}`,
        ),
      );
      body.append(tr);
    }
    const totals = el('tr', { cls: 'total' });
    const totalLabel = el('th', { text: 'Column total' });
    totalLabel.scope = 'row';
    totals.append(totalLabel);
    for (const column of grid.columns)
      totals.append(
        testid(
          el('td', { cls: 'num', text: String(grid.columnTotals.get(column.key) ?? 0) }),
          `column-total-${column.key}`,
        ),
      );
    totals.append(testid(el('td', { cls: 'num', text: String(grid.grandTotal) }), 'grand-total'));
    body.append(totals);
    const table = testid(el('table', { cls: 'matrix' }, el('thead', {}, cornerHead), body), 'matrix-table');
    if (matrix.methodological) table.classList.add('methodological');
    return el('div', { cls: 'scroll' }, table);
  }

  function cellView(
    matrix: MigrationMatrixView,
    grid: Grid,
    row: Axis,
    column: Axis,
    names: ReadonlyMap<string, string>,
  ): HTMLElement {
    const cell = grid.cells.get(`${row.key}>${column.key}`);
    const count = cell?.customer_count ?? 0;
    const td = el('td', { cls: count === 0 ? 'num zero' : 'num' });
    // The movement is the service's word, shown beside the count by the stylesheet, so the cell's text stays a number.
    if (cell !== undefined) {
      td.dataset.movement = cell.movement;
      td.dataset.label = cell.movement.replace('_', ' ');
      td.classList.add(`move-${cell.movement}`);
    }
    const id = `cell-${row.key}-${column.key}`;
    const opener = button(String(count), id, () => {
      void openMembers({
        title: `Customers who were ${row.name} and are ${column.name}`,
        query: {
          previous_state: row.ref.state,
          ...(row.ref.label_code === undefined ? {} : { previous_label_code: row.ref.label_code }),
          new_state: column.ref.state,
          ...(column.ref.label_code === undefined ? {} : { new_label_code: column.ref.label_code }),
        },
        expected: count,
        matrix,
        names,
      });
    });
    opener.setAttribute(
      'aria-label',
      `Open the ${count} customers who were ${row.name} and are ${column.name}`,
    );
    td.append(opener);
    return td;
  }

  function reconciliationView(matrix: MigrationMatrixView, nameOf: (key: string) => string): HTMLElement {
    const result = reconcile(matrix);
    if (result.ok)
      return testid(
        el('p', {
          cls: 'notice',
          text: `Totals reconcile: each row adds up to its before count, each column to its after count, and the ${matrix.customer_count} customers to the matrix total.`,
        }),
        'matrix-reconciled',
      );
    const list = el('ul');
    for (const m of result.mismatches)
      list.append(testid(el('li', { text: describeMismatch(m, nameOf) }), 'matrix-mismatch'));
    return testid(
      el(
        'div',
        { cls: 'warning' },
        el('p', { text: 'The totals do not reconcile. Do not rely on this matrix.' }),
        list,
      ),
      'matrix-unreconciled',
    );
  }

  function totalsView(matrix: MigrationMatrixView, names: ReadonlyMap<string, string>): HTMLElement {
    const body = el('tbody');
    for (const t of matrix.totals) {
      const r = ref(t.state, t.label_code);
      const key = stateKey(r);
      const tr = el('tr');
      tr.append(el('td', { text: stateName(r, 'before', names) }));
      tr.append(
        el('td', { cls: 'num', text: String(t.before_count) }),
        el('td', { cls: 'num', text: String(t.after_count) }),
      );
      for (const direction of ['entries', 'exits'] as const) {
        const count = t[direction];
        const td = el('td', { cls: 'num' });
        // The service lists a label's entries and exits; Unassigned and New have none to open.
        if (t.state === 'label' && t.label_code !== undefined) {
          const code = t.label_code;
          const name = stateName(r, 'before', names);
          const opener = button(String(count), `${direction}-${key}`, () => {
            void openMembers({
              title: direction === 'entries' ? `Customers who entered ${name}` : `Customers who left ${name}`,
              query: { direction, label_code: code },
              expected: count,
              matrix,
              names,
            });
          });
          opener.setAttribute(
            'aria-label',
            `Open the ${count} customers who ${direction === 'entries' ? 'entered' : 'left'} ${name}`,
          );
          td.append(opener);
        } else td.textContent = String(count);
        tr.append(td);
      }
      tr.dataset.state = key;
      body.append(tr);
    }
    return el(
      'div',
      { cls: 'scroll' },
      testid(el('table', {}, head(['State', 'Before', 'After', 'Entries', 'Exits']), body), 'totals-table'),
    );
  }

  interface MembersSpec {
    title: string;
    query: Pick<
      MigrationListQuery,
      'previous_state' | 'previous_label_code' | 'new_state' | 'new_label_code' | 'direction' | 'label_code'
    >;
    /** What the matrix counted for it; the list must hold as many (RN-59: the list is the matrix's own result). */
    expected: number;
    matrix: MigrationMatrixView;
    names: ReadonlyMap<string, string>;
  }

  async function openMembers(spec: MembersSpec): Promise<void> {
    latestExplanation.begin();
    explainPanel.replaceChildren();
    const kind = select(METHODOLOGICAL, '');
    const area = el('div');
    const request = (page: number): MigrationListQuery => ({
      ...spec.query,
      from: spec.matrix.from,
      to: spec.matrix.to,
      ...(kind.value === '' ? {} : { methodological: kind.value === 'true' }),
      page,
      size: PAGE_SIZE,
    });
    const load = (page: number): Promise<void> =>
      latestMembers(
        () => api.listMigrations(request(page)),
        (res) => {
          if (!res.ok) return void area.replaceChildren(errorBox(res.error));
          area.replaceChildren(...membersView(res.value, spec, kind.value === '', load));
        },
      );
    kind.addEventListener('change', () => void load(1));
    membersPanel.replaceChildren(
      testid(el('h3', { text: spec.title }), 'members-title'),
      formField('methodological', 'Show', kind, [], 'members'),
      area,
    );
    area.replaceChildren(testid(el('p', { text: 'Loading…' }), 'members-loading'));
    await load(1);
  }

  function membersView(
    list: MigrationListView,
    spec: MembersSpec,
    unfiltered: boolean,
    go: (page: number) => Promise<void>,
  ): Node[] {
    const parts: Node[] = [];
    if (unfiltered && list.page.total !== spec.expected)
      parts.push(
        testid(
          el('p', {
            cls: 'warning',
            text: `The matrix counted ${spec.expected} customers here but the list holds ${list.page.total}.`,
          }),
          'members-mismatch',
        ),
      );
    else if (unfiltered)
      parts.push(
        testid(
          el('p', {
            cls: 'notice',
            text: `The list holds all ${spec.expected} customers the matrix counted here.`,
          }),
          'members-reconciled',
        ),
      );
    if (list.items.length === 0) {
      parts.push(testid(el('p', { cls: 'notice', text: 'No customers match.' }), 'members-empty'));
      return parts;
    }
    const body = el('tbody');
    for (const m of list.items) {
      const tr = el('tr');
      tr.dataset.customerId = m.customer_id;
      const stateOf = (side: 'before' | 'after'): string =>
        side === 'before'
          ? stateName(ref(m.previous_state, m.previous_label_code), 'before', spec.names)
          : stateName(ref(m.new_state, m.new_label_code), 'after', spec.names);
      tr.append(
        el('td', { text: m.customer_id }),
        el('td', { text: stateOf('before') }),
        el('td', { text: stateOf('after') }),
        el('td', { text: m.movement }),
        el('td', { text: m.changed_at ?? 'before the earlier date' }),
        el('td', { text: m.cause ?? '' }),
        testid(el('td', { text: responsibleText(m) }), `responsible-${m.customer_id}`),
        testid(
          el('td', { text: m.methodological ? 'Methodological: may come from the model' : 'Behaviour' }),
          `kind-${m.customer_id}`,
        ),
        el(
          'td',
          {},
          button('Explain', `explain-${m.customer_id}`, () => void explain(m, spec)),
        ),
      );
      if (m.methodological) tr.classList.add('methodological');
      body.append(tr);
    }
    parts.push(
      el(
        'div',
        { cls: 'scroll' },
        testid(
          el(
            'table',
            {},
            head([
              'Customer',
              'Before',
              'After',
              'Movement',
              'Changed at',
              'Cause',
              'Responsible variables',
              'Kind',
              '',
            ]),
            body,
          ),
          'members-table',
        ),
      ),
      pager(list.page, 'customers', (page) => void go(page)),
    );
    return parts;
  }

  async function explain(m: MigrationView, spec: MembersSpec): Promise<void> {
    explainPanel.replaceChildren(testid(el('p', { text: 'Loading…' }), 'explanation-loading'));
    await latestExplanation(
      () => api.explainMigration({ customer_id: m.customer_id, from: spec.matrix.from, to: spec.matrix.to }),
      (res) => {
        if (!res.ok) return void explainPanel.replaceChildren(errorBox(res.error));
        explainPanel.replaceChildren(...explanationView(res.value, spec));
      },
    );
  }

  function explanationView(e: MigrationExplanationView, spec: MembersSpec): Node[] {
    const m = e.migration;
    const before = stateName(ref(m.previous_state, m.previous_label_code), 'before', spec.names);
    const after = stateName(ref(m.new_state, m.new_label_code), 'after', spec.names);
    const parts: Node[] = [
      el('h3', { text: 'Why this customer moved' }),
      facts([
        ['Customer', m.customer_id, 'explanation-customer'],
        ['Move', `${before} to ${after} (${m.movement})`, 'explanation-move'],
        ['Between', `${e.from} and ${e.to}`, 'explanation-window'],
        ['Responsible variables', responsibleText(m), 'explanation-responsible'],
      ]),
    ];
    if (m.methodological)
      parts.push(
        testid(
          el('p', {
            cls: 'warning methodological',
            text: 'Methodological: this move may come from the model, not from what the customer did (ADR-0064).',
          }),
          'explanation-methodological',
        ),
      );
    if (m.rank_only)
      parts.push(
        testid(
          el('p', {
            cls: 'notice',
            text: 'Only the rank moved: no variable changed. The score moved because the population or the model did.',
          }),
          'explanation-rank-only',
        ),
      );
    const body = el('tbody');
    for (const x of e.measures) {
      const judgement =
        x.judgement === 'changed'
          ? 'Changed'
          : x.judgement === 'stable'
            ? x.rank_changed
              ? 'Stable (the rank moved)'
              : 'Stable'
            : `Not compared (${(x.not_compared_reason === undefined ? undefined : REASONS[x.not_compared_reason]) ?? x.not_compared_reason ?? 'no value'})`;
      const tr = el(
        'tr',
        {},
        el('td', { text: variableName(x.variable) }),
        el('td', { text: x.unit }),
        el('td', { cls: 'num', text: x.previous_value ?? '' }),
        el('td', { cls: 'num', text: x.new_value ?? '' }),
        el('td', { text: judgement }),
        el('td', { text: x.statement }),
      );
      tr.dataset.variable = x.variable;
      tr.dataset.judgement = x.judgement;
      body.append(tr);
    }
    parts.push(
      el(
        'div',
        { cls: 'scroll' },
        testid(
          el('table', {}, head(['Variable', 'Unit', 'Before', 'After', 'Judgement', 'Statement']), body),
          'explanation-table',
        ),
      ),
    );
    return parts;
  }
}
