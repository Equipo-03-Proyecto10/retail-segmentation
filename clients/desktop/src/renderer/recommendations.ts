import type {
  DecisionQuery,
  DecisionSummaryView,
  InspectedItemView,
  InspectionView,
  InspectQuery,
} from '../shared/api';
import { RECOMMENDATION_OPERATIONS } from '../shared/modules';
import {
  availabilityText,
  dayAfter,
  dayStart,
  exclusionText,
  factorsText,
  kindText,
  named,
  ORDER_NOTE,
  reasonText,
  statusLabel,
  statusText,
  STATUSES,
  storeSourceText,
} from '../shared/recommendations';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, formField, markDetails, pager, select, testid, textInput } from './dom';
import { latestRequest } from './latest-request';

// The recommendations inspection (F28-08, #877): a customer, a store and a channel, and for each
// item its factors, its availability and its reasons, with alternatives and products held by another
// store marked as such; and the decisions the service recorded, opened to show what was offered,
// what was excluded and why. The service decides all of it (ADR-0067): this screen says it in plain
// words and never derives a kind, a figure or an exclusion. Everything the service sent is set with
// textContent, never as HTML.

const DECISION_PAGE_SIZE = 20;
const FIELDS = ['customer_id', 'store_id', 'channel_id', 'limit'];

const loading = (id: string): HTMLElement => testid(el('p', { text: 'Loading…' }), id);

function facts(rows: [string, string, string][], prefix: string): HTMLElement {
  const dl = el('dl', { cls: 'facts' });
  for (const [name, value, id] of rows)
    dl.append(el('dt', { text: name }), testid(el('dd', { text: value }), `${prefix}-${id}`));
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

function option(value: string, text: string): HTMLOptionElement {
  return Object.assign(el('option', { text }), { value });
}

function itemRow(item: InspectedItemView, v: InspectionView): HTMLElement {
  const tr = el(
    'tr',
    { cls: `kind-${item.kind}` },
    el('td', { cls: 'num', text: String(item.position) }),
    el('td', { text: named(item.product, 'Product') }),
    testid(el('td', { text: kindText(item, v.store) }), 'item-kind'),
    el('td', { text: factorsText(item.factors) }),
    el('td', { text: availabilityText(item.availability, item.kind) }),
    el('td', { text: item.reasons.map(reasonText).join('; ') }),
  );
  tr.dataset.kind = item.kind;
  tr.dataset.productId = String(item.product.id);
  return tr;
}

/** What the service answered, or what it recorded: the same view, headed for its source. */
function inspectionView(v: InspectionView, source: 'answer' | 'decision', extra: Node[] = []): Node[] {
  const p = source;
  const parts: Node[] = [];
  parts.push(el('h3', { text: source === 'answer' ? 'Recommendation' : 'Stored decision' }));
  const status = el('p', {
    cls: v.status === 'PERSONALIZATION_WITHDRAWN' ? 'warning' : 'notice',
    text: `${statusLabel(v.status)}. ${statusText(v.status)}`,
  });
  status.setAttribute('role', 'status');
  parts.push(testid(status, `${p}-status`));

  const rows: [string, string, string][] = [
    ['Decision', v.decision_id, 'decision-id'],
    ['Customer', v.customer_id ?? 'Anonymized: the customer id was removed and the figures stay', 'customer'],
    ['Computed at', v.computed_at, 'computed'],
    [
      'Judged at store',
      v.store === undefined
        ? 'none: no store was named and the customer has no usual store'
        : `${named(v.store, 'Store')}, ${storeSourceText(v.store_source)}`,
      'store',
    ],
    [
      'Channel',
      v.channel === undefined
        ? 'none named: availability is judged over any channel the store enables'
        : named(v.channel, 'Channel'),
      'channel',
    ],
  ];
  if (v.usual_store !== undefined) rows.push(['Usual store', named(v.usual_store, 'Store'), 'usual-store']);
  const segment = [
    v.label_code === undefined ? undefined : `label ${v.label_code}`,
    v.run_id === undefined ? undefined : `run ${v.run_id}`,
    v.model_version_id === undefined ? undefined : `model version ${v.model_version_id}`,
  ].filter((x) => x !== undefined);
  rows.push(['Segment', segment.length === 0 ? 'none known' : segment.join(', '), 'segment']);
  if (v.window !== undefined)
    rows.push(['Window', `${v.window.days} days, ${v.window.start} to ${v.window.end}`, 'window']);
  parts.push(facts(rows, p));

  parts.push(el('h4', { text: 'Offered' }));
  if (v.items.length === 0) {
    parts.push(testid(el('p', { cls: 'notice', text: 'No product was offered.' }), `${p}-empty`));
  } else {
    const body = el('tbody');
    for (const item of v.items) body.append(itemRow(item, v));
    parts.push(
      el('p', { text: ORDER_NOTE }),
      el(
        'div',
        { cls: 'scroll' },
        testid(
          el(
            'table',
            { cls: 'items' },
            head(['#', 'Product', 'Kind', 'Factors', 'Availability', 'Why']),
            body,
          ),
          `${p}-items`,
        ),
      ),
    );
  }

  parts.push(el('h4', { text: 'Excluded' }));
  if (v.exclusions.length === 0) {
    parts.push(
      testid(
        el('p', { cls: 'notice', text: 'Nothing that matched a signal was excluded.' }),
        `${p}-no-exclusions`,
      ),
    );
  } else {
    const list = el('dl', { cls: 'facts' });
    for (const e of v.exclusions) {
      const detail = el('dd', {
        text: `${e.count} ${e.count === 1 ? 'product' : 'products'}: ${exclusionText(e.reason)}`,
      });
      detail.dataset.reason = e.reason;
      list.append(el('dt', { text: e.reason }), detail);
    }
    parts.push(
      testid(list, `${p}-exclusions`),
      el('p', {
        text: 'Products are counted, never listed. A product replaced by an alternative is still counted.',
      }),
    );
  }
  parts.push(...extra);
  return parts;
}

export function renderRecommendations(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const mayInspect = ctx.can(RECOMMENDATION_OPERATIONS.inspect);
  const mayList = ctx.can(RECOMMENDATION_OPERATIONS.decisions);
  const mayOpen = ctx.can(RECOMMENDATION_OPERATIONS.decision);
  const latestAnswer = latestRequest();
  const latestFinder = latestRequest();
  const latestList = latestRequest();
  const latestDecision = latestRequest();

  const answerArea = testid(el('div'), 'inspection');
  const decisionArea = testid(el('div'), 'decision');
  const listArea = el('div');

  // ----- the inspection form -----
  const customer = textInput('');
  customer.autocomplete = 'off';
  customer.spellcheck = false;
  const store = select([['', "The customer's usual store"]], '');
  const channel = select([['', 'Any channel']], '');
  const limit = textInput('10', 'number');
  limit.min = '1';
  limit.max = '50';
  const inspect = el('button', { text: 'Inspect' });
  inspect.type = 'submit';
  inspect.dataset.testid = 'inspect';
  const form = testid(
    el(
      'form',
      { cls: 'filters' },
      formField('customer_id', 'Customer id', customer, [], 'rc'),
      formField('store_id', 'Store', store, [], 'rc'),
      formField('channel_id', 'Channel', channel, [], 'rc'),
      formField('limit', 'At most (1 to 50)', limit, [], 'rc'),
      inspect,
    ),
    'inspect-form',
  );
  form.noValidate = true;
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    void run();
  });

  async function run(): Promise<void> {
    const query: InspectQuery = { customer_id: customer.value.trim() };
    if (store.value !== '') query.store_id = Number(store.value);
    if (channel.value !== '') query.channel_id = Number(channel.value);
    if (limit.value.trim() !== '') query.limit = Number(limit.value);
    markDetails(form, []);
    answerArea.replaceChildren(loading('inspection-loading'));
    await latestAnswer(
      () => api.inspectRecommendation(query),
      (res) => {
        if (!res.ok) {
          markDetails(form, res.error.details);
          return void answerArea.replaceChildren(errorBox(res.error, FIELDS));
        }
        const open: Node[] = mayOpen
          ? [
              button(
                'Open the stored decision',
                'open-stored-decision',
                () => void openDecision(res.value.decision_id),
              ),
            ]
          : [];
        answerArea.replaceChildren(...inspectionView(res.value, 'answer', open));
      },
    );
  }

  // ----- finding a customer, for roles that may search -----
  const finder = el('div');
  if (ctx.can('searchCustomers')) {
    const text = textInput('');
    const results = el('ul', { cls: 'choices-list' });
    const picked = testid(el('p', { cls: 'notice' }), 'picked-customer');
    picked.hidden = true;
    const search = el('button', { text: 'Find customer' });
    search.type = 'submit';
    const findForm = testid(
      el(
        'form',
        { cls: 'filters' },
        formField('q', 'Find a customer (name, email or phone)', text, [], 'rf'),
        search,
      ),
      'customer-finder',
    );
    findForm.noValidate = true;
    findForm.addEventListener('submit', (event) => {
      event.preventDefault();
      const q = text.value.trim();
      if (q === '')
        return void results.replaceChildren(el('li', { text: 'Type a name, an email or a phone.' }));
      results.replaceChildren(el('li', { text: 'Loading…' }));
      void latestFinder(
        () => api.searchCustomers({ q, page: 1, size: 10 }),
        (res) => {
          if (!res.ok) return void results.replaceChildren(el('li', {}, errorBox(res.error)));
          if (res.value.items.length === 0)
            return void results.replaceChildren(el('li', { text: 'No customer matches.' }));
          results.replaceChildren(
            ...res.value.items.map((c) =>
              el(
                'li',
                {},
                button(
                  c.email === undefined ? c.name : `${c.name} · ${c.email}`,
                  `pick-customer-${c.customer_id}`,
                  () => {
                    customer.value = c.customer_id;
                    picked.textContent = `Customer chosen: ${c.name} (${c.customer_id})`;
                    picked.hidden = false;
                  },
                ),
              ),
            ),
          );
        },
      );
    });
    finder.append(findForm, results, picked);
  }

  // ----- the stored decisions -----
  const decisionCustomer = textInput('');
  decisionCustomer.autocomplete = 'off';
  decisionCustomer.spellcheck = false;
  const decisionStore = select([['', 'Any store']], '');
  const decisionStatus = select(
    [['', 'Any status'], ...STATUSES.map((s): [string, string] => [s, statusLabel(s)])],
    '',
  );
  const from = textInput('', 'date');
  const to = textInput('', 'date');
  const filter = el('button', { text: 'Search decisions' });
  filter.type = 'submit';
  const filters = testid(
    el(
      'form',
      { cls: 'filters' },
      formField('customer_id', 'Customer id', decisionCustomer, [], 'rd'),
      formField('store_id', 'Store', decisionStore, [], 'rd'),
      formField('status', 'Status', decisionStatus, [], 'rd'),
      formField('from', 'From (UTC date)', from, [], 'rd'),
      formField('to', 'To (UTC date, included)', to, [], 'rd'),
      filter,
    ),
    'decision-filters',
  );
  filters.noValidate = true;
  let query: DecisionQuery = {};
  filters.addEventListener('submit', (event) => {
    event.preventDefault();
    query = {};
    if (decisionCustomer.value.trim() !== '') query.customer_id = decisionCustomer.value.trim();
    if (decisionStore.value !== '') query.store_id = Number(decisionStore.value);
    if (decisionStatus.value !== '') query.status = decisionStatus.value;
    if (from.value !== '') query.from = dayStart(from.value);
    if (to.value !== '') query.to = dayAfter(to.value);
    markDetails(filters, []);
    void loadDecisions(1);
  });

  const byId = textInput('');
  byId.autocomplete = 'off';
  byId.spellcheck = false;
  const openById = el('button', { text: 'Open decision' });
  openById.type = 'submit';
  const byIdForm = testid(
    el('form', { cls: 'filters' }, formField('decision_id', 'Decision id', byId, [], 'ri'), openById),
    'decision-by-id',
  );
  byIdForm.noValidate = true;
  byIdForm.addEventListener('submit', (event) => {
    event.preventDefault();
    void openDecision(byId.value.trim());
  });

  async function loadDecisions(page: number): Promise<void> {
    listArea.replaceChildren(loading('decisions-loading'));
    await latestList(
      () => api.listRecommendationDecisions({ ...query, page, size: DECISION_PAGE_SIZE }),
      (res) => {
        if (!res.ok) {
          markDetails(filters, res.error.details);
          return void listArea.replaceChildren(
            errorBox(res.error, ['customer_id', 'store_id', 'status', 'from', 'to']),
          );
        }
        if (res.value.items.length === 0)
          return void listArea.replaceChildren(
            testid(el('p', { cls: 'notice', text: 'No decision matches.' }), 'decisions-empty'),
          );
        const body = el('tbody');
        for (const d of res.value.items) body.append(decisionRow(d));
        listArea.replaceChildren(
          el(
            'div',
            { cls: 'scroll' },
            testid(
              el(
                'table',
                {},
                head(['Computed at', 'Customer', 'Status', 'Store', 'Channel', 'Items', 'Excluded', '']),
                body,
              ),
              'decisions-table',
            ),
          ),
          pager(res.value.page, 'decisions', (next) => void loadDecisions(next)),
        );
      },
    );
  }

  function decisionRow(d: DecisionSummaryView): HTMLElement {
    const tr = el(
      'tr',
      {},
      el('td', { text: d.computed_at }),
      el('td', { text: d.customer_id ?? 'Anonymized' }),
      el('td', { text: statusLabel(d.status) }),
      el('td', {
        text:
          d.store === undefined ? 'none' : `${named(d.store, 'Store')}, ${storeSourceText(d.store_source)}`,
      }),
      el('td', { text: d.channel === undefined ? 'any' : named(d.channel, 'Channel') }),
      el('td', { cls: 'num', text: String(d.item_count) }),
      el('td', {
        text:
          d.exclusions.length === 0 ? 'none' : d.exclusions.map((e) => `${e.count} ${e.reason}`).join(', '),
      }),
      el(
        'td',
        {},
        ...(mayOpen
          ? [button('Open', `open-decision-${d.decision_id}`, () => void openDecision(d.decision_id))]
          : []),
      ),
    );
    tr.dataset.decisionId = d.decision_id;
    return tr;
  }

  async function openDecision(decisionId: string): Promise<void> {
    markDetails(byIdForm, []);
    decisionArea.replaceChildren(loading('decision-loading'));
    if (typeof decisionArea.scrollIntoView === 'function') decisionArea.scrollIntoView({ block: 'nearest' });
    await latestDecision(
      () => api.openRecommendationDecision(decisionId),
      (res) => {
        if (!res.ok) return void decisionArea.replaceChildren(errorBox(res.error));
        decisionArea.replaceChildren(...inspectionView(res.value, 'decision'));
      },
    );
  }

  // ----- the stores and channels both forms choose from -----
  if (ctx.can('listStores'))
    void api.stores().then((r) => {
      if (!r.ok) return;
      for (const s of r.value) {
        const text = `${s.name} (#${s.store_id})`;
        store.append(option(String(s.store_id), text));
        decisionStore.append(option(String(s.store_id), text));
      }
    });
  if (ctx.can('listChannels'))
    void api.listChannels().then((r) => {
      if (!r.ok) return;
      for (const c of r.value) channel.append(option(String(c.id), c.name));
    });

  const sections: Node[] = [];
  if (mayInspect)
    sections.push(
      el(
        'section',
        { cls: 'panel' },
        el('h3', { text: 'Inspect a recommendation' }),
        el('p', {
          text: "Choose a customer, and optionally the store and channel the products are judged at. With no store the customer's usual store is used, and with no channel any channel the store enables.",
        }),
        finder,
        form,
        answerArea,
      ),
    );
  if (mayList)
    sections.push(
      el(
        'section',
        { cls: 'panel' },
        el('h3', { text: 'Stored decisions' }),
        el('p', {
          text: 'Every recommendation the service gave is recorded. Open one to see what was offered, what was excluded and why.',
        }),
        filters,
        listArea,
        ...(mayOpen ? [byIdForm] : []),
        decisionArea,
      ),
    );
  else
    sections.push(
      testid(
        el('p', { cls: 'notice', text: 'Your role may not read the stored decisions.' }),
        'decisions-unavailable',
      ),
    );
  host.replaceChildren(el('h2', { text: 'Recommendations' }), ...sections);
  if (mayList) void loadDecisions(1);
}
