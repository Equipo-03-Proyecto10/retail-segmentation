import type { PlanningQuery, PlanningRowView, PlanningView } from '../shared/api';
import { SYNTHETIC_LABEL } from '../shared/report-table';
import { categoryOptions, type ScreenContext } from './catalog';
import { el, errorBox, formField, pager, select, testid, textInput } from './dom';
import { storePicker } from './floor';
import { latestRequest } from './latest-request';

// Inventory planning (F28-12, #881): for a window and a store, demand by segment and category
// beside the stock on hand, days of cover and the units needed. The service computes the figures
// and states the calculation, which is shown as it comes. Segments are counts only.

const DAY = 86_400_000;
/** Four weeks ending yesterday: full days only, as the service requires. */
const DEFAULT_WINDOW_DAYS = 28;
const DEFAULT_TARGET_DAYS = 14;

const isoDay = (t: number): string => new Date(t).toISOString().slice(0, 10);

/** A ratio of four places as a percentage of one place. */
const percent = (share: string): string => `${(Number(share) * 100).toFixed(1)}%`;

export function segmentsText(row: PlanningRowView): string {
  if (row.segments.length === 0) return '—';
  return row.segments.map((s) => `${s.segment}: ${s.net_units} (${percent(s.share)})`).join('; ');
}

function planningTable(rows: PlanningRowView[]): HTMLTableElement {
  const headers = [
    'Store',
    'Category',
    'Demand (units)',
    'Daily demand',
    'On hand',
    'Days of cover',
    'To replenish',
    'Demand by segment',
    'Origin',
  ];
  const head = el('tr');
  headers.forEach((h) => head.append(el('th', { text: h })));
  const body = el('tbody');
  for (const r of rows) {
    const tr = el('tr');
    const cells: [string, string?][] = [
      [`${r.store} (#${r.store_id})`],
      [`${r.category} (#${r.category_id})`],
      [String(r.net_units), 'num'],
      [r.daily_demand, 'num'],
      [String(r.on_hand), 'num'],
      [r.days_of_cover ?? 'No demand', 'num'],
      [String(r.replenishment_units), 'num'],
      [segmentsText(r)],
      [r.synthetic ? SYNTHETIC_LABEL : 'Observed', r.synthetic ? 'synthetic' : undefined],
    ];
    for (const [text, cls] of cells) {
      const td = el('td', { text });
      if (cls !== undefined) td.className = cls;
      tr.append(td);
    }
    if (r.replenishment_units > 0) tr.className = 'short';
    tr.dataset.store = String(r.store_id);
    tr.dataset.category = String(r.category_id);
    body.append(tr);
  }
  return testid(el('table', {}, el('thead', {}, head), body), 'planning-table');
}

function planningView(view: PlanningView, go: (page: number) => void): HTMLElement {
  const parts: Node[] = [];
  if (view.synthetic)
    parts.push(
      testid(
        el('p', {
          cls: 'warning',
          text: `${SYNTHETIC_LABEL}: some demand in this report comes from seeded data (ADR-0052).`,
        }),
        'planning-synthetic',
      ),
    );
  const facts = el('dl');
  const fact = (name: string, value: string, id: string): void => {
    facts.append(el('dt', { text: name }), testid(el('dd', { text: value }), id));
  };
  fact('Window', `${view.from} to ${view.to} (${view.window_days} days)`, 'planning-window');
  fact('Target cover', `${view.target_cover_days} days`, 'planning-target');
  fact('Stock read at', view.generated_at, 'planning-generated');
  if (view.store_restricted) fact('Stores', 'Only the stores assigned to you', 'planning-scope');
  const short = view.rows.filter((r) => r.replenishment_units > 0);
  fact(
    'Needs on this page',
    short.length === 0
      ? 'Every row is covered'
      : `${short.length} of ${view.rows.length} rows, ${short.reduce((n, r) => n + r.replenishment_units, 0)} units`,
    'planning-needs',
  );
  parts.push(el('h3', { text: 'Summary' }), testid(facts, 'planning-summary'));
  parts.push(
    el('h3', { text: 'Demand and availability' }),
    view.rows.length === 0
      ? testid(el('p', { cls: 'notice', text: 'No demand or stock for these filters.' }), 'planning-empty')
      : el('div', { cls: 'scroll' }, planningTable(view.rows)),
    pager(view.page, 'rows', go),
  );
  parts.push(
    el('h3', { text: 'How needs are calculated' }),
    testid(el('p', { text: view.calculation }), 'planning-calculation'),
  );
  return el('section', {}, ...parts);
}

export function renderPlanning(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const output = el('div');
  const latest = latestRequest();
  const yesterday = Date.now() - DAY;
  const from = textInput(isoDay(yesterday - (DEFAULT_WINDOW_DAYS - 1) * DAY), 'date');
  const to = textInput(isoDay(yesterday), 'date');
  const store = storePicker(ctx, 'Every store');
  const category = select([['', 'Every category']], '');
  const target = textInput(String(DEFAULT_TARGET_DAYS), 'number');
  target.min = '1';
  target.max = '180';
  void api.categoryTree().then((r) => {
    if (!r.ok) return;
    for (const [value, text] of categoryOptions(r.value))
      category.append(Object.assign(el('option', { text }), { value }));
  });

  let query: PlanningQuery | undefined;
  const load = (): void => {
    if (query === undefined) return;
    const q = query;
    output.replaceChildren(testid(el('p', { text: 'Loading…' }), 'planning-loading'));
    void latest(
      () => api.planningReport(q),
      (r) => {
        if (!r.ok) return void output.replaceChildren(errorBox(r.error));
        output.replaceChildren(
          planningView(r.value, (page) => {
            query = { ...q, page };
            load();
          }),
        );
      },
    );
  };

  const controls = testid(el('form', { cls: 'filters' }), 'planning-filters');
  controls.noValidate = true;
  const run = el('button', { text: 'Show plan' });
  run.type = 'submit';
  run.dataset.testid = 'planning-open';
  controls.addEventListener('submit', (event) => {
    event.preventDefault();
    const next: PlanningQuery = { from: from.value, to: to.value, page: 1 };
    if (store.value !== '') next.store_id = Number(store.value);
    if (category.value !== '') next.category_id = Number(category.value);
    if (target.value.trim() !== '') next.target_cover_days = Number(target.value);
    query = next;
    load();
  });
  controls.append(
    formField('from', 'From', from, [], 'p'),
    formField('to', 'To', to, [], 'p'),
    formField('store_id', 'Store', store, [], 'p'),
    formField('category_id', 'Category', category, [], 'p'),
    formField('target_cover_days', 'Target cover (days)', target, [], 'p'),
    run,
  );
  host.replaceChildren(el('h2', { text: 'Inventory planning' }), controls, output);
}
