import type {
  CheckInDetailView,
  CheckInsQuery,
  CheckInView,
  EngagementEventView,
  EventsQuery,
  GroupBy,
  ReviewView,
  SalesQuery,
  SaleView,
  StockQuery,
  StoreView,
  SummaryQuery,
} from '../shared/api';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, formField, markDetails, pager, select, show, testid, textInput } from './dom';
import { latestRequest } from './latest-request';

// What happened on the floor (F16-04, #417). Each screen reads through a named
// bridge operation. The two corrections the back office may make, voiding an
// event and reviewing a check-in, are drawn only for roles allowed to call them.

function table(id: string, headers: string[], rows: HTMLTableRowElement[]): HTMLTableElement {
  const head = el('tr');
  for (const h of headers) head.append(el('th', { text: h }));
  const body = el('tbody');
  body.append(...rows);
  return testid(el('table', {}, el('thead', {}, head), body), id);
}

function row(cells: (string | Node)[], key?: [string, string]): HTMLTableRowElement {
  const tr = el('tr');
  for (const c of cells) tr.append(typeof c === 'string' ? el('td', { text: c }) : el('td', {}, c));
  if (key) tr.dataset[key[0]] = key[1];
  return tr;
}

function facts(entries: [string, string, string][]): HTMLElement {
  const dl = el('dl');
  for (const [name, value, id] of entries)
    dl.append(el('dt', { text: name }), testid(el('dd', { text: value }), id));
  return dl;
}

function form(id: string, children: Node[], onSubmit: (form: HTMLFormElement) => void): HTMLFormElement {
  const f = testid(el('form', { cls: 'filters' }, ...children), id);
  f.noValidate = true;
  f.addEventListener('submit', (event) => {
    event.preventDefault();
    onSubmit(f);
  });
  return f;
}

function submit(text: string): HTMLButtonElement {
  const b = el('button', { text });
  b.type = 'submit';
  return b;
}

/** A store picker filled from stores-service; the first option means any store. */
function storePicker(ctx: ScreenContext, any: string): HTMLSelectElement {
  const picker = select([['', any]], '');
  void ctx.api.stores().then((res) => {
    if (!res.ok) return;
    for (const s of res.value)
      picker.append(Object.assign(el('option', { text: storeLabel(s) }), { value: String(s.store_id) }));
  });
  return picker;
}

const storeLabel = (s: StoreView): string => `${s.name} (#${s.store_id})`;
const numberOrUndefined = (v: string): number | undefined => (v === '' ? undefined : Number(v));
const textOrUndefined = (v: string): string | undefined => (v.trim() === '' ? undefined : v.trim());
const yesNo = (v: string): boolean | undefined => (v === '' ? undefined : v === 'true');
/** A datetime-local value read as UTC, which is how the services compare times. */
const dateTime = (v: string): string | undefined => (v === '' ? undefined : `${v}:00Z`);
const SIZES: [string, string][] = [
  ['20', '20 a page'],
  ['50', '50 a page'],
  ['100', '100 a page'],
];

// ---------- stock ----------

export function renderStock(host: HTMLElement, ctx: ScreenContext): void {
  const results = el('div');
  const renderLatest = latestRequest();
  const store = storePicker(ctx, 'Every store');
  const below = textInput('', 'number');
  const size = select(SIZES, '20');
  let query: StockQuery = { page: 1, size: 20 };

  async function load(): Promise<void> {
    await renderLatest(
      () => ctx.api.stockLevels(query),
      (res) => {
        if (!res.ok) return void results.replaceChildren(errorBox(res.error));
        results.replaceChildren(
          table(
            'stock-table',
            ['Product', 'Store', 'On hand', 'Updated'],
            res.value.items.map((l) =>
              row(
                [
                  `${l.product_name} (#${l.product_id})`,
                  l.store_name,
                  String(l.quantity_on_hand),
                  l.updated_at,
                ],
                ['productId', String(l.product_id)],
              ),
            ),
          ),
          pager(res.value.page, 'levels', (page) => {
            query = { ...query, page };
            void load();
          }),
        );
      },
    );
  }

  const filters = form(
    'stock-filters',
    [
      formField('store_id', 'Store', store, [], 'filter'),
      formField('below', 'Below this quantity', below, [], 'filter'),
      formField('size', 'Page size', size, [], 'filter'),
      submit('Show stock'),
    ],
    () => {
      query = { page: 1, size: Number(size.value) };
      const s = numberOrUndefined(store.value);
      if (s !== undefined) query.store_id = s;
      const b = numberOrUndefined(below.value);
      if (b !== undefined) query.below = b;
      void load();
    },
  );
  host.replaceChildren(el('h2', { text: 'Stock' }), filters, results);
  void load();
}

// ---------- sales ----------

export function renderSales(host: HTMLElement, ctx: ScreenContext): void {
  const results = el('div');
  const detail = testid(el('section', { cls: 'panel' }), 'sale-detail');
  const renderLatestList = latestRequest();
  const renderLatestDetail = latestRequest();
  const customer = textInput('');
  const store = storePicker(ctx, 'Every store');
  const from = textInput('', 'datetime-local');
  const to = textInput('', 'datetime-local');
  let query: SalesQuery = { page: 1, size: 20 };

  async function load(): Promise<void> {
    await renderLatestList(
      () => ctx.api.listSales(query),
      (res) => {
        if (!res.ok) return void results.replaceChildren(errorBox(res.error));
        results.replaceChildren(
          table(
            'sales-table',
            ['Sale', 'When', 'Store', 'Channel', 'Customer', 'Total', ''],
            res.value.items.map((s) =>
              row(
                [
                  String(s.sale_id),
                  s.occurred_at,
                  s.store_name,
                  s.channel_name,
                  s.customer_id,
                  s.total,
                  button('View', `view-sale-${s.sale_id}`, () => void open(s.sale_id)),
                ],
                ['saleId', String(s.sale_id)],
              ),
            ),
          ),
          pager(res.value.page, 'sales', (page) => {
            query = { ...query, page };
            void load();
          }),
        );
      },
    );
  }

  async function open(saleId: number): Promise<void> {
    await renderLatestDetail(
      () => ctx.api.getSale(saleId),
      (res) => {
        if (!res.ok) return void detail.replaceChildren(errorBox(res.error));
        showSale(res.value);
      },
    );
  }

  function showSale(s: SaleView): void {
    detail.replaceChildren(
      el('h3', { text: `Sale ${s.sale_id}` }),
      facts([
        ['When', s.occurred_at, 'sale-when'],
        ['Store', s.store_name, 'sale-store'],
        ['Channel', s.channel_name, 'sale-channel'],
        ['Customer', s.customer_id, 'sale-customer'],
        ['Total', s.total, 'sale-total'],
      ]),
      table(
        'sale-lines',
        ['Product', 'Quantity', 'Unit price', 'Line total'],
        s.lines.map((l) =>
          row([`${l.product_name} (#${l.product_id})`, String(l.quantity), l.unit_price, l.line_total]),
        ),
      ),
    );
  }

  const filters = form(
    'sales-filters',
    [
      formField('customer_id', 'Customer ID', customer, [], 'filter'),
      formField('store_id', 'Store', store, [], 'filter'),
      formField('from', 'From (UTC)', from, [], 'filter'),
      formField('to', 'To (UTC)', to, [], 'filter'),
      submit('Search'),
    ],
    () => {
      query = { page: 1, size: 20 };
      const c = textOrUndefined(customer.value);
      if (c !== undefined) query.customer_id = c;
      const s = numberOrUndefined(store.value);
      if (s !== undefined) query.store_id = s;
      const f = dateTime(from.value);
      if (f !== undefined) query.from = f;
      const t = dateTime(to.value);
      if (t !== undefined) query.to = t;
      void load();
    },
  );
  host.replaceChildren(
    el('h2', { text: 'Sales' }),
    button('Refresh', 'refresh-sales', () => void load()),
    filters,
    results,
    detail,
  );
  void load();
}

// ---------- engagement ----------

export function renderEngagement(host: HTMLElement, ctx: ScreenContext): void {
  const summaryArea = testid(el('div'), 'summary');
  const message = testid(el('div'), 'engagement-message');
  const eventsArea = el('div');
  const detail = testid(el('section', { cls: 'panel' }), 'event-detail');
  const renderLatestSummary = latestRequest();
  const renderLatestEvents = latestRequest();
  const renderLatestDetail = latestRequest();

  const sFrom = textInput('', 'date');
  const sTo = textInput('', 'date');
  const sStore = storePicker(ctx, 'Every store');
  const groupBy = select(
    [
      ['store', 'Store'],
      ['day', 'Day'],
      ['type', 'Type'],
    ],
    'store',
  );
  let summaryQuery: SummaryQuery = { group_by: 'store' };

  async function loadSummary(): Promise<void> {
    await renderLatestSummary(
      () => ctx.api.engagementSummary(summaryQuery),
      (res) => {
        if (!res.ok) return void summaryArea.replaceChildren(errorBox(res.error));
        const v = res.value;
        const heading = { store: 'Store', day: 'Day', type: 'Type' }[v.group_by];
        summaryArea.replaceChildren(
          table(
            'summary-table',
            [heading, 'Events'],
            v.groups.map((g) => row([g.label, String(g.count)], ['key', g.key])),
          ),
        );
      },
    );
  }

  const summaryForm = form(
    'summary-filters',
    [
      formField('from', 'From', sFrom, [], 'summary'),
      formField('to', 'To', sTo, [], 'summary'),
      formField('store_id', 'Store', sStore, [], 'summary'),
      formField('group_by', 'Group by', groupBy, [], 'summary'),
      submit('Show summary'),
    ],
    () => {
      summaryQuery = { group_by: groupBy.value as GroupBy };
      if (sFrom.value) summaryQuery.from = sFrom.value;
      if (sTo.value) summaryQuery.to = sTo.value;
      const s = numberOrUndefined(sStore.value);
      if (s !== undefined) summaryQuery.store_id = s;
      void loadSummary();
    },
  );

  const parts: Node[] = [
    el('h2', { text: 'Engagement' }),
    el('h3', { text: 'Summary' }),
    summaryForm,
    summaryArea,
  ];

  // The event list is offered only to roles that may read events (not INVENTORY_PLANNER).
  if (ctx.can('listEngagementEvents')) {
    const type = select(
      [
        ['', 'Any type'],
        ['QR_SCAN', 'QR scan'],
        ['RECO_CLICK', 'Recommendation click'],
      ],
      '',
    );
    const eStore = storePicker(ctx, 'Every store');
    const voided = select(
      [
        ['', 'Voided or not'],
        ['false', 'Not voided'],
        ['true', 'Voided'],
      ],
      '',
    );
    const eFrom = textInput('', 'datetime-local');
    const eTo = textInput('', 'datetime-local');
    let eventsQuery: EventsQuery = { page: 1, size: 20 };

    const loadEvents = async (): Promise<void> => {
      await renderLatestEvents(
        () => ctx.api.listEngagementEvents(eventsQuery),
        (res) => {
          if (!res.ok) return void eventsArea.replaceChildren(errorBox(res.error));
          eventsArea.replaceChildren(
            table(
              'events-table',
              ['Type', 'Product', 'Store', 'When', 'Status', ''],
              res.value.items.map((e) =>
                row(
                  [
                    e.type,
                    `${e.product_name} (#${e.product_id})`,
                    e.store_name,
                    e.occurred_at,
                    e.voided ? 'Voided' : 'Counted',
                    button('View', `view-event-${e.event_id}`, () => showEvent(e)),
                  ],
                  ['eventId', e.event_id],
                ),
              ),
            ),
            pager(res.value.page, 'events', (page) => {
              eventsQuery = { ...eventsQuery, page };
              void loadEvents();
            }),
          );
        },
      );
    };

    const showEvent = (e: EngagementEventView): void => {
      renderLatestDetail.begin();
      const entries: [string, string, string][] = [
        ['Type', e.type, 'event-type'],
        ['Product', `${e.product_name} (#${e.product_id})`, 'event-product'],
        ['Store', e.store_name, 'event-store'],
        ['Occurred', e.occurred_at, 'event-when'],
        ['Status', e.voided ? 'Voided' : 'Counted', 'event-status'],
      ];
      if (e.customer_id !== undefined) entries.push(['Customer', e.customer_id, 'event-customer']);
      if (e.label_code !== undefined) entries.push(['Segment label', e.label_code, 'event-label']);
      if (e.rank !== undefined) entries.push(['Rank', String(e.rank), 'event-rank']);
      const items: Node[] = [el('h3', { text: 'Event' }), facts(entries)];
      if (e.history.length > 0) {
        const list = testid(el('ol'), 'event-history');
        for (const h of e.history)
          list.append(el('li', { text: `${h.action} at ${h.at} by ${h.by}: ${h.reason}` }));
        items.push(el('h4', { text: 'History' }), list);
      }
      // A void corrects the counts; it never deletes the event. There is no delete control.
      if (!e.voided && ctx.can('voidEngagementEvent')) items.push(voidForm(e));
      detail.replaceChildren(...items);
    };

    const voidForm = (e: EngagementEventView): HTMLElement => {
      const why = el('textarea');
      const area = el('div');
      const confirm = button('Confirm void', 'confirm-void', () => {
        const isCurrent = renderLatestDetail.begin();
        void ctx.api.voidEngagementEvent(e.event_id, why.value).then(async (res) => {
          if (!isCurrent()) return;
          show(message, res, 'The event is voided. It stays listed, and the summary no longer counts it.', [
            'reason',
          ]);
          if (res.ok) showEvent(res.value);
          else markDetails(f, res.error.details);
          await Promise.all([loadEvents(), loadSummary()]);
        });
      });
      const f = form('void-form', [formField('reason', 'Reason for voiding', why, [])], () => undefined);
      f.className = '';
      f.append(
        button('Void', 'void', () => {
          area.replaceChildren(el('p', { text: 'The event stays in the list, marked voided.' }), confirm);
        }),
        area,
      );
      return f;
    };

    const eventsForm = form(
      'event-filters',
      [
        formField('type', 'Type', type, [], 'events'),
        formField('store_id', 'Store', eStore, [], 'events'),
        formField('voided', 'Voided', voided, [], 'events'),
        formField('from', 'From (UTC)', eFrom, [], 'events'),
        formField('to', 'To (UTC)', eTo, [], 'events'),
        submit('Show events'),
      ],
      () => {
        eventsQuery = { page: 1, size: 20 };
        if (type.value) eventsQuery.type = type.value as 'QR_SCAN' | 'RECO_CLICK';
        const s = numberOrUndefined(eStore.value);
        if (s !== undefined) eventsQuery.store_id = s;
        const v = yesNo(voided.value);
        if (v !== undefined) eventsQuery.voided = v;
        const f = dateTime(eFrom.value);
        if (f !== undefined) eventsQuery.from = f;
        const t = dateTime(eTo.value);
        if (t !== undefined) eventsQuery.to = t;
        void loadEvents();
      },
    );
    parts.push(el('h3', { text: 'Events' }), message, eventsForm, eventsArea, detail);
    void loadEvents();
  }
  host.replaceChildren(...parts);
  void loadSummary();
}

// ---------- check-ins ----------

/** The server's verdict in plain words, as the floor app words it, from the back office's side. */
export function describeReason(c: CheckInView): string {
  const distance =
    c.distance_m === undefined ? '' : ` (${Math.round(Number(c.distance_m))} m from the store)`;
  switch (c.reason) {
    case 'within_geofence':
      return `Inside the store's geofence${distance}`;
    case 'outside_geofence':
      return `Outside the store's geofence${distance}`;
    case 'low_accuracy':
      return `Location too imprecise to tell${c.accuracy_m === undefined ? '' : ` (within ${Math.round(Number(c.accuracy_m))} m)`}`;
    case 'store_location_unknown':
      return 'The store has no location set, so it could not be measured';
    case 'no_fix':
      return 'No location was sent, so it could not be measured';
    default:
      return c.reason;
  }
}

export function renderCheckIns(host: HTMLElement, ctx: ScreenContext): void {
  const message = testid(el('div'), 'check-ins-message');
  const results = el('div');
  const detail = testid(el('section', { cls: 'panel' }), 'check-in-detail');
  const renderLatestList = latestRequest();
  const renderLatestDetail = latestRequest();
  const store = storePicker(ctx, 'Every store');
  const verified = select(
    [
      ['', 'Verified or not'],
      ['true', 'Verified'],
      ['false', 'Not verified'],
    ],
    '',
  );
  const from = textInput('', 'datetime-local');
  const to = textInput('', 'datetime-local');
  let query: CheckInsQuery = { page: 1, size: 20 };

  async function load(): Promise<void> {
    await renderLatestList(
      () => ctx.api.listCheckIns(query),
      (res) => {
        if (!res.ok) return void results.replaceChildren(errorBox(res.error));
        results.replaceChildren(
          table(
            'check-ins-table',
            ['When', 'Store', 'User', 'Result', 'Verified', ''],
            res.value.items.map((c) =>
              row(
                [
                  c.occurred_at,
                  c.store_name,
                  c.user_id,
                  describeReason(c),
                  c.verified ? 'Yes' : 'No',
                  button('View', `view-check-in-${c.check_in_id}`, () => void open(c.check_in_id)),
                ],
                ['checkInId', c.check_in_id],
              ),
            ),
          ),
          pager(res.value.page, 'check-ins', (page) => {
            query = { ...query, page };
            void load();
          }),
        );
      },
    );
  }

  async function open(checkInId: string): Promise<void> {
    await renderLatestDetail(
      () => ctx.api.getCheckIn(checkInId),
      (res) => {
        if (!res.ok) return void detail.replaceChildren(errorBox(res.error));
        showCheckIn(res.value);
      },
    );
  }

  function reviewItem(r: ReviewView): HTMLElement {
    const li = el('li', {
      text: `${r.reviewed_at}: ${r.verified ? 'Verified' : 'Not verified'} by ${r.reviewed_by}. ${r.reason}`,
    });
    li.dataset.reviewId = String(r.review_id);
    return li;
  }

  function showCheckIn(c: CheckInDetailView): void {
    const entries: [string, string, string][] = [
      ['When', c.occurred_at, 'check-in-when'],
      ['Store', c.store_name, 'check-in-store'],
      ['User', c.user_id, 'check-in-user'],
      ['Result', describeReason(c), 'check-in-reason'],
      ['Reason code', c.reason, 'check-in-reason-code'],
      ['Verified', c.verified ? 'Yes' : 'No', 'check-in-verified'],
      ['Verified automatically', c.auto_verified ? 'Yes' : 'No', 'check-in-auto'],
    ];
    if (c.latitude !== undefined)
      entries.push(['Position', `${c.latitude}, ${c.longitude}`, 'check-in-position']);
    if (c.accuracy_m !== undefined) entries.push(['Accuracy', `${c.accuracy_m} m`, 'check-in-accuracy']);
    if (c.distance_m !== undefined) entries.push(['Distance', `${c.distance_m} m`, 'check-in-distance']);
    const reviews = testid(el('ol'), 'reviews');
    reviews.append(...c.reviews.map(reviewItem));
    const items: Node[] = [
      el('h3', { text: 'Check-in' }),
      facts(entries),
      el('h4', { text: 'Reviews' }),
      c.reviews.length === 0 ? el('p', { text: 'No review yet.' }) : reviews,
    ];
    // A review is appended below the earlier ones; nothing edits or deletes a check-in or a review.
    if (ctx.can('createCheckInReview')) items.push(reviewForm(c));
    detail.replaceChildren(...items);
  }

  function reviewForm(c: CheckInDetailView): HTMLElement {
    const verdict = select(
      [
        ['true', 'Verified'],
        ['false', 'Not verified'],
      ],
      'true',
    );
    const why = el('textarea');
    const f = form(
      'review-form',
      [
        el('h4', { text: 'Add a review' }),
        formField('verified', 'Verdict', verdict, []),
        formField('reason', 'Reason', why, []),
        submit('Add review'),
      ],
      () => {
        const isCurrent = renderLatestDetail.begin();
        void ctx.api.reviewCheckIn(c.check_in_id, verdict.value === 'true', why.value).then(async (res) => {
          if (!isCurrent()) return;
          show(message, res, 'Review added.', ['verified', 'reason']);
          if (res.ok) {
            await open(c.check_in_id);
            await load();
          } else {
            markDetails(f, res.error.details);
          }
        });
      },
    );
    f.className = '';
    return f;
  }

  const filters = form(
    'check-in-filters',
    [
      formField('store_id', 'Store', store, [], 'filter'),
      formField('verified', 'Verified', verified, [], 'filter'),
      formField('from', 'From (UTC)', from, [], 'filter'),
      formField('to', 'To (UTC)', to, [], 'filter'),
      submit('Search'),
    ],
    () => {
      query = { page: 1, size: 20 };
      const s = numberOrUndefined(store.value);
      if (s !== undefined) query.store_id = s;
      const v = yesNo(verified.value);
      if (v !== undefined) query.verified = v;
      const f = dateTime(from.value);
      if (f !== undefined) query.from = f;
      const t = dateTime(to.value);
      if (t !== undefined) query.to = t;
      void load();
    },
  );
  host.replaceChildren(el('h2', { text: 'Check-ins' }), message, filters, results, detail);
  void load();
}
