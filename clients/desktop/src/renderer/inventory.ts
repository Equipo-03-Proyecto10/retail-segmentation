import type { CategoryPlanView, InventoryPlanView, SegmentPlanView } from '../shared/api';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, formField, select, testid, textInput } from './dom';
import { latestRequest } from './latest-request';

// Demand and stock (F28-12, #881): a store's demand for a period by category and by segment, next
// to the units on hand. The plan holds no customer, and "short by" is a planning hint, not a forecast.

const DAY = 86_400_000;
const isoDay = (t: number): string => new Date(t).toISOString().slice(0, 10);

function categoryTable(rows: CategoryPlanView[]): HTMLElement {
  const head = el('tr');
  for (const h of ['Category', 'Demand (units)', 'Sales', 'Amount', 'On hand', 'Short by'])
    head.append(el('th', { text: h }));
  const body = el('tbody');
  for (const r of rows) {
    const tr = el('tr');
    tr.append(
      el('td', { text: r.category }),
      el('td', { cls: 'num', text: String(r.demand_units) }),
      el('td', { cls: 'num', text: String(r.demand_orders) }),
      el('td', { cls: 'num', text: r.demand_amount }),
      el('td', { cls: 'num', text: String(r.on_hand) }),
      el('td', { cls: 'num', text: String(r.short_by) }),
    );
    body.append(tr);
  }
  return testid(el('table', {}, el('thead', {}, head), body), 'inventory-categories');
}

function segmentTable(rows: SegmentPlanView[]): HTMLElement {
  const head = el('tr');
  for (const h of ['Segment', 'Demand (units)', 'Sales', 'Amount']) head.append(el('th', { text: h }));
  const body = el('tbody');
  for (const r of rows) {
    const tr = el('tr');
    tr.append(
      el('td', { text: r.label }),
      el('td', { cls: 'num', text: String(r.units) }),
      el('td', { cls: 'num', text: String(r.orders) }),
      el('td', { cls: 'num', text: r.amount }),
    );
    body.append(tr);
  }
  return testid(el('table', {}, el('thead', {}, head), body), 'inventory-segments');
}

function planView(plan: InventoryPlanView): HTMLElement {
  const parts: Node[] = [];
  if (plan.synthetic)
    parts.push(
      testid(
        el('p', {
          cls: 'warning',
          text: 'Synthetic: some figures in this plan come from seeded data (ADR-0052).',
        }),
        'inventory-synthetic',
      ),
    );
  if (plan.truncated)
    parts.push(
      testid(
        el('p', {
          cls: 'warning',
          text: 'The plan is cut: a list was longer than it reads. Some figures may be short.',
        }),
        'inventory-truncated',
      ),
    );
  parts.push(
    el('h3', { text: `${plan.store_name} (#${plan.store_id}), ${plan.from} to ${plan.to}` }),
    el('p', {
      cls: 'notice',
      text: 'Short by is the period’s demand in units above the units on hand today. It is a planning hint, not a forecast. The plan holds no customer.',
    }),
  );
  parts.push(el('h3', { text: 'By category' }));
  parts.push(
    plan.categories.length === 0
      ? testid(el('p', { cls: 'notice', text: 'No demand or stock for these filters.' }), 'inventory-empty')
      : el('div', { cls: 'scroll' }, categoryTable(plan.categories)),
  );
  parts.push(el('h3', { text: 'By segment' }));
  parts.push(
    plan.segments.length === 0
      ? el('p', { cls: 'notice', text: 'No demand by segment for these filters.' })
      : el('div', { cls: 'scroll' }, segmentTable(plan.segments)),
  );
  return el('section', {}, ...parts);
}

export function renderInventory(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const message = el('div');
  message.setAttribute('role', 'status');
  const output = el('div');
  const controls = el('form');
  controls.noValidate = true;
  const latest = latestRequest();
  host.replaceChildren(el('h2', { text: 'Demand and stock' }), controls, message, output);

  const store = select([['', 'Choose a store']], '');
  const today = Date.now();
  const from = textInput(isoDay(today - 30 * DAY), 'date');
  const to = textInput(isoDay(today), 'date');
  void api.stores().then((r) => {
    if (!r.ok) return void message.replaceChildren(errorBox(r.error));
    for (const s of r.value)
      store.append(
        Object.assign(el('option', { text: `${s.name} (#${s.store_id})` }), { value: String(s.store_id) }),
      );
  });

  const run = button('Open plan', 'inventory-open', () => {
    message.replaceChildren();
    if (store.value === '') {
      output.replaceChildren();
      return void message.replaceChildren(
        errorBox({ code: 'invalid_input', message: 'Choose a store.', correlation_id: '-', details: [] }),
      );
    }
    output.replaceChildren(testid(el('p', { text: 'Loading…' }), 'inventory-loading'));
    void latest(
      () => api.inventoryPlan({ store_id: Number(store.value), from: from.value, to: to.value }),
      (r) => {
        if (!r.ok) return void output.replaceChildren(errorBox(r.error));
        output.replaceChildren(planView(r.value));
      },
    );
  });
  controls.addEventListener('submit', (event) => {
    event.preventDefault();
    run.click();
  });
  controls.replaceChildren(
    formField('store_id', 'Store', store, [], 'r'),
    formField('from', 'From', from, [], 'r'),
    formField('to', 'To', to, [], 'r'),
    run,
  );
}
