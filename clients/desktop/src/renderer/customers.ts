import type {
  AssignmentRowView,
  ChoiceView,
  CurrentAssignmentView,
  CustomerQuery,
  CustomerView,
  PreferencesView,
  RecommendationView,
  SegmentVocabularyView,
} from '../shared/api';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, formField, markDetails, pager, select, show, testid, textInput } from './dom';
import { latestRequest } from './latest-request';

// Customers, both preference sets, the segment and the recommendations (F16-03, #414).
// Each panel shows what its own service returned: a state or a reason is printed as the
// service wrote it, and nothing is filled in from another source.

const CUSTOMER_FIELDS = ['name', 'email', 'phone', 'registration_channel_id'];

const SIZES: [string, string][] = [
  ['20', '20 a page'],
  ['50', '50 a page'],
  ['100', '100 a page'],
];

const scoresOf = (r: { r_score?: number; f_score?: number; m_score?: number }): string =>
  r.r_score === undefined ? 'none' : `R ${r.r_score} · F ${r.f_score} · M ${r.m_score}`;

const labelOf = (r: { label_code?: string; label_name?: string }): string =>
  r.label_code === undefined ? 'none' : `${r.label_name ?? '(not in the vocabulary)'} (${r.label_code})`;

function facts(rows: [string, string, string][]): HTMLElement {
  const dl = el('dl');
  for (const [name, value, id] of rows)
    dl.append(el('dt', { text: name }), testid(el('dd', { text: value }), id));
  return dl;
}

export function renderCustomers(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const message = testid(el('div'), 'customers-message');
  const tableArea = el('div');
  const detail = testid(el('section', { cls: 'panel' }), 'customer-detail');
  const prefsPanel = testid(el('section', { cls: 'panel' }), 'preferences-panel');
  const segmentPanel = testid(el('section', { cls: 'panel' }), 'segment-panel');
  const historyArea = testid(el('div'), 'history-area');
  const recsPanel = testid(el('section', { cls: 'panel' }), 'recommendations-panel');
  const labelsPanel = testid(el('section', { cls: 'panel' }), 'labels-panel');
  const membersArea = testid(el('div'), 'members-area');
  const lists = latestRequest();
  const renderDetail = latestRequest();
  const renderPrefs = latestRequest();
  const renderSegment = latestRequest();
  const renderHistory = latestRequest();
  const renderRecs = latestRequest();
  const renderLabels = latestRequest();
  const renderMembers = latestRequest();
  let query: CustomerQuery = { page: 1, size: 20 };
  let channels: ChoiceView[] = [];
  let vocabulary: SegmentVocabularyView | null = null;
  // The registration channels are read once; a customer's form waits for them.
  const channelsReady = api.listChannels().then((res) => {
    if (res.ok) channels = res.value;
  });

  const text = textInput('');
  const email = textInput('', 'text');
  const size = select(SIZES, '20');
  const search = el('button', { text: 'Search' });
  search.type = 'submit';
  const filters = testid(
    el(
      'form',
      { cls: 'filters' },
      formField('q', 'Name, email or phone', text, [], 'filter'),
      formField('email', 'Exact email', email, [], 'filter'),
      formField('size', 'Page size', size, [], 'filter'),
      search,
    ),
    'customer-filters',
  );
  filters.noValidate = true;
  filters.addEventListener('submit', (event) => {
    event.preventDefault();
    query = { page: 1, size: Number(size.value) };
    if (text.value.trim()) query.q = text.value.trim();
    if (email.value.trim()) query.email = email.value.trim();
    void load();
  });

  async function load(): Promise<void> {
    await lists(
      () => api.searchCustomers(query),
      (res) => {
        if (!res.ok) return void tableArea.replaceChildren(errorBox(res.error));
        const head = el('tr');
        for (const h of ['Name', 'Email', 'Phone', 'Registered', ''] as const)
          head.append(el('th', { text: h }));
        const body = el('tbody');
        for (const c of res.value.items) {
          const row = el(
            'tr',
            {},
            el('td', { text: c.name }),
            el('td', { text: c.email ?? '' }),
            el('td', { text: c.phone ?? '' }),
            el('td', { text: c.registered_on }),
            el(
              'td',
              {},
              button('View', `view-customer-${c.customer_id}`, () => void open(c.customer_id)),
            ),
          );
          row.dataset.customerId = c.customer_id;
          body.append(row);
        }
        tableArea.replaceChildren(
          testid(el('table', {}, el('thead', {}, head), body), 'customers-table'),
          pager(res.value.page, 'customers', (page) => {
            query = { ...query, page };
            void load();
          }),
        );
      },
    );
  }

  /** Opens a customer. Each panel keeps its own latest request, so a slow answer for an earlier customer is dropped. */
  async function open(customerId: string): Promise<void> {
    const loading = (): HTMLElement => el('p', { text: 'Loading…' });
    detail.replaceChildren(loading());
    prefsPanel.replaceChildren(loading());
    segmentPanel.replaceChildren(loading());
    recsPanel.replaceChildren(loading());
    historyArea.replaceChildren();
    renderHistory.begin();
    await Promise.all([
      renderDetail(
        async () => {
          await channelsReady;
          return api.getCustomer(customerId);
        },
        (res) => {
          if (!res.ok) return void detail.replaceChildren(errorBox(res.error));
          showCustomer(res.value);
        },
      ),
      renderPrefs(
        () => api.getPreferences(customerId),
        (res) => {
          if (!res.ok)
            return void prefsPanel.replaceChildren(el('h3', { text: 'Preferences' }), errorBox(res.error));
          showPreferences(res.value);
        },
      ),
      renderSegment(
        () => api.currentAssignment(customerId),
        (res) => {
          if (!res.ok)
            return void segmentPanel.replaceChildren(el('h3', { text: 'Segment' }), errorBox(res.error));
          showSegment(res.value);
        },
      ),
      renderRecs(
        () => api.getRecommendations(customerId),
        (res) => {
          if (!res.ok)
            return void recsPanel.replaceChildren(el('h3', { text: 'Recommendations' }), errorBox(res.error));
          showRecommendations(res.value);
        },
      ),
    ]);
  }

  function showCustomer(c: CustomerView): void {
    const parts: Node[] = [
      el('h3', { text: c.name }),
      facts([
        ['Customer id', c.customer_id, 'customer-id'],
        ['Name', c.name, 'customer-name'],
        ['Email', c.email ?? 'none', 'customer-email'],
        ['Phone', c.phone ?? 'none', 'customer-phone'],
        ['Registration channel', c.registration_channel_name, 'customer-channel'],
        ['Registered on', c.registered_on, 'customer-registered'],
      ]),
    ];
    if (ctx.can('correctCustomer')) parts.push(customerForm(c));
    detail.replaceChildren(...parts);
  }

  function customerForm(c: CustomerView): HTMLElement {
    const name = textInput(c.name);
    const mail = textInput(c.email ?? '');
    const phone = textInput(c.phone ?? '');
    const options: [string, string][] = channels.map((ch) => [String(ch.id), ch.name]);
    if (!options.some(([v]) => v === String(c.registration_channel_id)))
      options.push([String(c.registration_channel_id), c.registration_channel_name]);
    const channel = select(options, String(c.registration_channel_id));
    const save = el('button', { text: 'Save changes' });
    save.type = 'submit';
    const form = testid(
      el(
        'form',
        {},
        el('h4', { text: 'Correct this customer' }),
        formField('name', 'Name', name, []),
        formField('email', 'Email', mail, []),
        formField('phone', 'Phone', phone, []),
        formField('registration_channel_id', 'Registration channel', channel, []),
        save,
      ),
      'customer-form',
    );
    form.noValidate = true;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      // Only what changed is sent: an absent field is left as it is.
      const changes: Record<string, string | number> = {};
      if (name.value !== c.name) changes.name = name.value;
      if (mail.value !== (c.email ?? '')) changes.email = mail.value;
      if (phone.value !== (c.phone ?? '')) changes.phone = phone.value;
      if (channel.value !== String(c.registration_channel_id))
        changes.registration_channel_id = Number(channel.value);
      const isCurrent = renderDetail.begin();
      void api.correctCustomer(c.customer_id, changes).then(async (res) => {
        if (!isCurrent()) return;
        show(message, res, `${c.name} saved.`, CUSTOMER_FIELDS);
        if (res.ok) showCustomer(res.value);
        else markDetails(form, res.error.details);
        await load();
      });
    });
    return form;
  }

  function showPreferences(p: PreferencesView): void {
    const names = (list: ChoiceView[]): string =>
      list.length === 0 ? 'none chosen' : list.map((x) => x.name).join(', ');
    const parts: Node[] = [
      el('h3', { text: 'Preferences' }),
      facts([
        ['Preferred channels', names(p.channels), 'pref-channels'],
        ['Interest categories', names(p.categories), 'pref-categories'],
      ]),
    ];
    // Every other role reads only: no control is drawn for them.
    if (ctx.can('replacePreferredChannels'))
      parts.push(
        setForm('channels-form', 'Preferred channels', 'channel', p.channel_options, p.channels, (ids) =>
          api.replacePreferredChannels(p.customer_id, ids),
        ),
      );
    if (ctx.can('replaceInterestCategories'))
      parts.push(
        setForm(
          'categories-form',
          'Interest categories',
          'category',
          p.category_options,
          p.categories,
          (ids) => api.replaceInterestCategories(p.customer_id, ids),
        ),
      );
    prefsPanel.replaceChildren(...parts);
  }

  /** The whole edited set is sent, never a partial list; unchecking everything clears it. */
  function setForm(
    id: string,
    title: string,
    kind: string,
    options: ChoiceView[],
    chosen: ChoiceView[],
    send: (ids: number[]) => ReturnType<typeof api.replacePreferredChannels>,
  ): HTMLElement {
    const all = [...options];
    for (const c of chosen) if (!all.some((o) => o.id === c.id)) all.push(c);
    const boxes = el('div', { cls: 'choices' });
    const checks: [number, HTMLInputElement][] = [];
    for (const o of all) {
      const box = el('input');
      box.type = 'checkbox';
      box.checked = chosen.some((c) => c.id === o.id);
      box.dataset.testid = `${kind}-option-${o.id}`;
      checks.push([o.id, box]);
      boxes.append(el('label', {}, box, el('span', { text: o.name })));
    }
    const save = el('button', { text: 'Save' });
    save.type = 'submit';
    const form = testid(el('form', {}, el('h4', { text: title }), boxes, save), id);
    form.noValidate = true;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const isCurrent = renderPrefs.begin();
      const ids = checks.filter(([, b]) => b.checked).map(([n]) => n);
      void send(ids).then((res) => {
        if (!isCurrent()) return;
        show(message, res, `${title} saved.`);
        if (res.ok) showPreferences(res.value);
        else prefsPanel.prepend(errorBox(res.error));
      });
    });
    return form;
  }

  function showSegment(a: CurrentAssignmentView): void {
    const rows: [string, string, string][] = [['State', a.state, 'segment-state']];
    if (a.label_code !== undefined) rows.push(['Label', labelOf(a), 'segment-label']);
    if (a.r_score !== undefined) rows.push(['RFM scores', scoresOf(a), 'segment-scores']);
    if (a.since !== undefined) rows.push(['Since', a.since, 'segment-since']);
    if (a.run_id !== undefined) rows.push(['Run', String(a.run_id), 'segment-run']);
    segmentPanel.replaceChildren(
      el('h3', { text: 'Segment' }),
      facts(rows),
      button('Show history', 'show-history', () => void loadHistory(a.customer_id, 1)),
      historyArea,
    );
  }

  async function loadHistory(customerId: string, page: number): Promise<void> {
    await renderHistory(
      () => api.listAssignments({ customer_id: customerId, page, size: 20 }),
      (res) => {
        if (!res.ok) return void historyArea.replaceChildren(errorBox(res.error));
        historyArea.replaceChildren(
          assignmentTable(res.value.items, 'history-table', false),
          pager(res.value.page, 'history', (next) => void loadHistory(customerId, next)),
        );
      },
    );
  }

  function assignmentTable(rows: AssignmentRowView[], id: string, withCustomer: boolean): HTMLElement {
    const head = el('tr');
    for (const h of [...(withCustomer ? ['Customer'] : []), 'Run', 'Label', 'Scores', 'From', 'To'])
      head.append(el('th', { text: h }));
    const body = el('tbody');
    for (const r of rows) {
      const cells: Node[] = [];
      if (withCustomer)
        cells.push(
          el(
            'td',
            {},
            button(r.customer_id, `open-member-${r.customer_id}`, () => void open(r.customer_id)),
          ),
        );
      cells.push(
        el('td', { text: String(r.run_id) }),
        el('td', { text: labelOf(r) }),
        el('td', { text: scoresOf(r) }),
        el('td', { text: r.valid_from }),
        el('td', { text: r.valid_to ?? 'open' }),
      );
      body.append(el('tr', {}, ...cells));
    }
    return testid(el('table', {}, el('thead', {}, head), body), id);
  }

  function showRecommendations(r: RecommendationView): void {
    const rows: [string, string, string][] = [
      ['Status', r.status, 'reco-status'],
      ['Computed at', r.computed_at, 'reco-computed'],
      ['Window', `${r.window.days} days, ${r.window.start} to ${r.window.end}`, 'reco-window'],
    ];
    if (r.label_code !== undefined) rows.push(['Label', r.label_code, 'reco-label']);
    if (r.run_id !== undefined) rows.push(['Run', String(r.run_id), 'reco-run']);
    if (r.usual_store_id !== undefined) rows.push(['Usual store', `Store ${r.usual_store_id}`, 'reco-store']);
    const parts: Node[] = [el('h3', { text: 'Recommendations' }), facts(rows)];
    if (r.items.length > 0) {
      const head = el('tr');
      for (const h of ['Product', 'Category', 'Held', 'Reasons']) head.append(el('th', { text: h }));
      const body = el('tbody');
      for (const i of r.items) {
        const reasons = i.reasons.map((x) => {
          const bits = [x.signal];
          if (x.segment_buyers !== undefined) bits.push(`${x.segment_buyers} segment buyers`);
          if (x.category_name !== undefined) bits.push(`nearest category ${x.category_name}`);
          if (x.share !== undefined) bits.push(`share ${x.share}`);
          return bits.join(', ');
        });
        const row = el(
          'tr',
          {},
          el('td', { text: i.product_name }),
          el('td', { text: i.category_name }),
          el('td', { text: String(i.quantity_on_hand) }),
          testid(el('td', { text: reasons.join('; ') }), `reco-reasons-${i.product_id}`),
        );
        row.dataset.productId = String(i.product_id);
        body.append(row);
      }
      parts.push(testid(el('table', {}, el('thead', {}, head), body), 'reco-table'));
    }
    recsPanel.replaceChildren(...parts);
  }

  async function loadLabels(): Promise<void> {
    await renderLabels(
      () => api.segmentVocabulary(),
      (res) => {
        if (!res.ok)
          return void labelsPanel.replaceChildren(el('h3', { text: 'Segment labels' }), errorBox(res.error));
        vocabulary = res.value;
        const list = el('ol', { cls: 'labels' });
        for (const l of res.value.labels)
          list.append(
            el(
              'li',
              {},
              button(
                `${l.name} (${l.label_code}) · ${l.size}`,
                `label-${l.label_code}`,
                () => void loadMembers(l.label_code, 1, true),
              ),
            ),
          );
        labelsPanel.replaceChildren(
          el('h3', { text: 'Segment labels' }),
          testid(
            el('p', { text: `${res.value.unassigned_count} customers unassigned in the latest run` }),
            'labels-unassigned',
          ),
          list,
          membersArea,
        );
      },
    );
  }

  async function loadMembers(label: string, page: number, openOnly: boolean): Promise<void> {
    await renderMembers(
      () => api.listAssignments({ label_code: label, open: openOnly, page, size: 20 }),
      (res) => {
        if (!res.ok) return void membersArea.replaceChildren(errorBox(res.error));
        const name = vocabulary?.labels.find((l) => l.label_code === label)?.name ?? label;
        const box = el('input');
        box.type = 'checkbox';
        box.checked = openOnly;
        box.dataset.testid = 'members-open-only';
        box.addEventListener('change', () => void loadMembers(label, 1, box.checked));
        membersArea.replaceChildren(
          el('h4', { text: `Members of ${name}` }),
          el('label', {}, box, el('span', { text: 'Current members only' })),
          assignmentTable(res.value.items, 'members-table', true),
          pager(res.value.page, 'members', (next) => void loadMembers(label, next, openOnly)),
        );
      },
    );
  }

  host.replaceChildren(
    el('h2', { text: 'Customers' }),
    filters,
    message,
    tableArea,
    detail,
    prefsPanel,
    segmentPanel,
    recsPanel,
    labelsPanel,
  );
  void load();
  void loadLabels();
}
