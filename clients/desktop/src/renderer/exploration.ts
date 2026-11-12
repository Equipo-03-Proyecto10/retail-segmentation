import type { CustomerView, ErrorView, Result } from '../shared/api';
import {
  agreement,
  DAY_MS,
  instant,
  windowText,
  type ConsentView,
  type ExplorationProfileView,
  type ExplorationSalesView,
  type FiguresView,
  type InterestsView,
  type NamedId,
  type PurchaseView,
  type RestrictionsView,
  type ShareView,
} from '../shared/exploration';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, formField, testid, textInput } from './dom';
import { latestRequest } from './latest-request';

// Customer exploration (F28-03, #872): one customer's behaviour, with the profile of rfm-engine set
// beside the sales over the same window and over a window the analyst chooses. Every figure names its
// window. A refusal, by the console from the contract's role list or by a service, is shown on the
// panel it belongs to and never hides the others. Everything the services sent is set with
// textContent, never as HTML.

const SEARCH_SIZE = 10;
const NOT_AVAILABLE = 'Not available';

/** A short account of who refused: the console from the contract's role list (no call was made), or the service. */
function refusalLead(title: string, error: ErrorView): string {
  const refused = error.code === 'forbidden_role' || error.code === 'forbidden_store';
  if (refused && error.correlation_id === '-')
    return `Your role may not read the ${title}. The console refused it before calling the service, from the role list of the contract. Nothing of it is shown.`;
  if (refused) return `The service refused your role: nothing of the ${title} is shown.`;
  if (error.code === 'audit_unavailable')
    return `The service could not record this read in the audit trail, so it served nothing: nothing of the ${title} is shown.`;
  return `The ${title} could not be read.`;
}

function refusal(title: string, error: ErrorView, id: string): HTMLElement {
  const lead = el('p', { cls: 'warning', text: refusalLead(title.toLowerCase(), error) });
  lead.setAttribute('role', 'status');
  return el('div', {}, testid(lead, `refusal-${id}`), errorBox(error));
}

function facts(rows: [string, string, string][]): HTMLElement {
  const dl = el('dl', { cls: 'exploration-facts' });
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

function cell(text: string, detail?: string, id?: string): HTMLElement {
  const td = el('td', { text });
  if (detail !== undefined) td.append(el('span', { cls: 'detail', text: detail }));
  return id === undefined ? td : testid(td, id);
}

const names = (list: readonly NamedId[]): string =>
  list.length === 0 ? 'none' : list.map((n) => n.name).join(', ');

const days = (n: number | undefined): string =>
  n === undefined ? 'no purchase in the window' : `${n} ${n === 1 ? 'day' : 'days'}`;

const STATUS: Record<PurchaseView['status'], string> = {
  counted: 'Counted',
  partly_reversed: 'Counted, part returned',
  reversed: 'Not counted: reversed in full',
};

const PURPOSES: Record<string, string> = {
  PERSONALIZATION: 'Personalization',
  MARKETING_CONTACT: 'Marketing contact',
  PROFILING: 'Profiling',
};

const BASIS: Record<string, string> = {
  EXPLICIT: 'Explicit (the customer said so)',
  PROGRAMME_DEFAULT: 'Programme default (not the customer’s own consent)',
};

export function renderExploration(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const latestSearch = latestRequest();
  const latestCustomer = latestRequest();
  const latestRestrictions = latestRequest();
  const latestConsent = latestRequest();
  const latestProfile = latestRequest();
  const latestSales = latestRequest();
  const latestInterests = latestRequest();

  const searchText = textInput('');
  const searchButton = el('button', { text: 'Search customers' });
  searchButton.type = 'submit';
  const searchForm = testid(
    el('form', { cls: 'filters' }, formField('q', 'Name or email', searchText, [], 'x'), searchButton),
    'exploration-search',
  );
  searchForm.noValidate = true;
  const searchArea = el('div');

  const today = Math.floor(Date.now() / DAY_MS) * DAY_MS;
  const customerId = textInput('');
  const fromDay = textInput(instant(today - 89 * DAY_MS).slice(0, 10), 'date');
  const toDay = textInput(instant(today).slice(0, 10), 'date');
  const exploreButton = el('button', { text: 'Explore' });
  exploreButton.type = 'submit';
  const form = testid(
    el(
      'form',
      { cls: 'filters' },
      formField('customer', 'Customer id', customerId, [], 'x'),
      formField('from', 'Sales window, first day (UTC)', fromDay, [], 'x'),
      formField('to', 'Sales window, last day (UTC, included)', toDay, [], 'x'),
      exploreButton,
    ),
    'exploration-form',
  );
  form.noValidate = true;

  const message = el('div');
  message.setAttribute('role', 'status');
  const panels = {
    customer: testid(el('section', { cls: 'panel' }), 'exploration-customer'),
    restrictions: testid(el('section', { cls: 'panel' }), 'exploration-restrictions'),
    consent: testid(el('section', { cls: 'panel' }), 'exploration-consent'),
    profile: testid(el('section', { cls: 'panel' }), 'exploration-profile'),
    figures: testid(el('section', { cls: 'panel' }), 'exploration-figures'),
    categories: testid(el('section', { cls: 'panel' }), 'exploration-categories'),
    channels: testid(el('section', { cls: 'panel' }), 'exploration-channels'),
    purchases: testid(el('section', { cls: 'panel' }), 'exploration-purchases'),
    interests: testid(el('section', { cls: 'panel' }), 'exploration-interests'),
  };

  host.replaceChildren(
    el('h2', { text: 'Customer exploration' }),
    el('p', {
      text: 'One customer’s purchases, recency, frequency, spend, categories and channels, each with the window it covers. The profile of rfm-engine is set beside the sales over its own window and over the window you choose. A window includes its first instant and excludes its last.',
    }),
    el('p', {
      cls: 'notice',
      text: 'Opening a customer reads that customer’s sales history. ADR-0066 (section 4) has the services record such a read in the audit trail, and a service that cannot record it serves nothing.',
    }),
    searchForm,
    searchArea,
    form,
    message,
    panels.customer,
    panels.restrictions,
    panels.profile,
    panels.figures,
    panels.categories,
    panels.channels,
    panels.purchases,
    panels.consent,
    panels.interests,
  );

  host.classList.add('exploration');

  // ---------- picking a customer ----------

  searchForm.addEventListener('submit', (event) => {
    event.preventDefault();
    void search();
  });

  async function search(): Promise<void> {
    const q = searchText.value.trim();
    searchArea.replaceChildren(el('p', { text: 'Loading…' }));
    await latestSearch(
      () => api.searchCustomers({ ...(q === '' ? {} : { q }), page: 1, size: SEARCH_SIZE }),
      (res) => {
        if (!res.ok) return void searchArea.replaceChildren(errorBox(res.error));
        if (res.value.items.length === 0)
          return void searchArea.replaceChildren(
            testid(el('p', { cls: 'notice', text: 'No customer matches.' }), 'exploration-search-empty'),
          );
        const body = el('tbody');
        for (const c of res.value.items) {
          const tr = el(
            'tr',
            {},
            el('td', { text: c.name }),
            el('td', { text: c.email ?? '' }),
            el('td', { text: c.registered_on }),
            el(
              'td',
              {},
              button('Explore', `explore-${c.customer_id}`, () => {
                customerId.value = c.customer_id;
                void explore();
              }),
            ),
          );
          tr.dataset.customerId = c.customer_id;
          body.append(tr);
        }
        searchArea.replaceChildren(
          el(
            'div',
            { cls: 'scroll' },
            testid(
              el('table', {}, head(['Name', 'Email', 'Registered', '']), body),
              'exploration-search-table',
            ),
          ),
        );
        // The picker shows the first page only; a longer list is narrowed by the text, not paged.
        if (res.value.page.total > res.value.items.length)
          searchArea.append(
            testid(
              el('p', {
                cls: 'notice',
                text: `Showing ${res.value.items.length} of ${res.value.page.total} customers. Narrow the search to find the one you want.`,
              }),
              'exploration-search-more',
            ),
          );
      },
    );
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    void explore();
  });

  // ---------- one customer ----------

  async function explore(): Promise<void> {
    const id = customerId.value.trim();
    message.replaceChildren();
    if (id === '') return void message.replaceChildren(problem('Choose a customer, or enter a customer id.'));
    if (fromDay.value === '' || toDay.value === '')
      return void message.replaceChildren(problem('Choose the first and the last day of the sales window.'));
    const from = Date.parse(`${fromDay.value}T00:00:00Z`);
    // The last day is shown as included, so the window ends where the next day starts.
    const to = Date.parse(`${toDay.value}T00:00:00Z`) + DAY_MS;
    if (Number.isNaN(from) || Number.isNaN(to) || from >= to)
      return void message.replaceChildren(problem('The last day cannot come before the first.'));
    const chosen = { from: instant(from), to: instant(to) };

    const loading = (): HTMLElement => el('p', { text: 'Loading…' });
    for (const p of Object.values(panels)) p.replaceChildren(loading());
    latestSales.begin();

    await Promise.all([
      latestCustomer(
        () => api.getCustomer(id),
        (res) => showCustomer(res),
      ),
      latestRestrictions(
        () => api.exploreRestrictions(id),
        (res) => showRestrictions(res),
      ),
      latestConsent(
        () => api.exploreConsent(id),
        (res) => showConsent(res),
      ),
      latestInterests(
        () => api.exploreInterests(id),
        (res) => showInterests(res),
      ),
      latestProfile(
        () => api.exploreProfile(id),
        (profile) => {
          showProfile(profile);
          // The sales are read over the profile's window as well as the chosen one, when the profile has one.
          void loadSales(id, chosen, profile, profile.ok ? profile.value.window : undefined);
        },
      ),
    ]);
  }

  function problem(text: string): HTMLElement {
    return testid(el('p', { cls: 'error', text }), 'exploration-problem');
  }

  async function loadSales(
    id: string,
    chosen: { from: string; to: string },
    profile: Result<ExplorationProfileView>,
    profileWindow: { from: string; to: string } | undefined,
  ): Promise<void> {
    await latestSales(
      () =>
        api.exploreSales({
          customer_id: id,
          from: chosen.from,
          to: chosen.to,
          ...(profileWindow === undefined
            ? {}
            : { compare_from: profileWindow.from, compare_to: profileWindow.to }),
        }),
      (sales) => {
        showFigures(profile, sales);
        showPurchases(sales);
        showShares(sales, 'categories');
        showShares(sales, 'channels');
      },
    );
  }

  function showCustomer(res: Result<CustomerView>): void {
    if (!res.ok)
      return void panels.customer.replaceChildren(
        el('h3', { text: 'Customer' }),
        refusal('Customer', res.error, 'customer'),
      );
    const c = res.value;
    panels.customer.replaceChildren(
      el('h3', { text: c.name }),
      facts([
        ['Customer id', c.customer_id, 'customer-id'],
        ['Registered on', c.registered_on, 'customer-registered'],
        ['Registration channel', c.registration_channel_name, 'customer-channel'],
      ]),
    );
  }

  // ---------- what the customer restricted and allowed ----------

  function showRestrictions(res: Result<RestrictionsView>): void {
    const title = 'Restrictions';
    if (!res.ok)
      return void panels.restrictions.replaceChildren(
        el('h3', { text: title }),
        refusal(title, res.error, 'restrictions'),
      );
    const r = res.value;
    const none = r.channels.length === 0 && r.categories.length === 0;
    panels.restrictions.replaceChildren(
      el('h3', { text: title }),
      r.personalization_allowed
        ? testid(
            el('p', {
              cls: 'notice',
              text: 'Personalization is allowed: the current PERSONALIZATION decision is granted.',
            }),
            'personalization-allowed',
          )
        : testid(
            el('p', {
              cls: 'warning',
              text: 'Personalization is not allowed: nothing is tailored to this customer, because their current PERSONALIZATION decision is not granted. It is withdrawn or was never recorded.',
            }),
            'personalization-not-allowed',
          ),
      facts([
        ['Channels not to reach them on', names(r.channels), 'restricted-channels'],
        ['Categories not to offer them, and those below', names(r.categories), 'restricted-categories'],
      ]),
      ...(none
        ? [
            testid(
              el('p', { cls: 'notice', text: 'No channel or category is restricted.' }),
              'no-restrictions',
            ),
          ]
        : []),
    );
  }

  function showConsent(res: Result<ConsentView>): void {
    const title = 'Consent record';
    if (!res.ok)
      return void panels.consent.replaceChildren(
        el('h3', { text: title }),
        refusal(title, res.error, 'consent'),
      );
    const body = el('tbody');
    for (const p of res.value.purposes) {
      const tr = el(
        'tr',
        {},
        el('td', { text: PURPOSES[p.purpose] ?? p.purpose }),
        testid(el('td', { text: p.state }), `consent-state-${p.purpose}`),
        el('td', { text: p.recorded_at ?? '' }),
        el('td', { text: p.recorded_by ?? '' }),
        el('td', { text: p.source ?? '' }),
        el('td', { text: p.basis === undefined ? '' : (BASIS[p.basis] ?? p.basis) }),
      );
      tr.dataset.purpose = p.purpose;
      body.append(tr);
    }
    panels.consent.replaceChildren(
      el('h3', { text: title }),
      el('p', { text: `As of ${res.value.as_of}. A purpose never recorded is not granted.` }),
      el(
        'div',
        { cls: 'scroll' },
        testid(
          el('table', {}, head(['Purpose', 'State', 'Recorded at', 'Recorded by', 'Source', 'Basis']), body),
          'consent-table',
        ),
      ),
    );
  }

  function showInterests(res: Result<InterestsView>): void {
    const title = 'Interests';
    if (!res.ok)
      return void panels.interests.replaceChildren(
        el('h3', { text: title }),
        refusal(title, res.error, 'interests'),
      );
    const body = el('tbody');
    for (const i of res.value.inferred)
      body.append(
        el(
          'tr',
          {},
          el('td', { text: i.category.name }),
          el('td', { cls: 'num', text: `${i.strength}%` }),
          el('td', { text: `${i.window_days} days` }),
          el('td', { text: i.inferred_at }),
          el('td', { text: i.source }),
        ),
      );
    panels.interests.replaceChildren(
      el('h3', { text: title }),
      facts([['Declared by the customer', names(res.value.declared), 'declared-interests']]),
      el('h4', { text: 'Inferred from net purchases, not the customer’s word' }),
      res.value.inferred.length === 0
        ? testid(
            el('p', {
              cls: 'notice',
              text: 'No interest is inferred. The service infers none unless the customer’s personalization is granted.',
            }),
            'no-inferred-interests',
          )
        : el(
            'div',
            { cls: 'scroll' },
            testid(
              el('table', {}, head(['Category', 'Strength', 'Window', 'Inferred at', 'Source']), body),
              'inferred-table',
            ),
          ),
    );
  }

  // ---------- the profile ----------

  function showProfile(res: Result<ExplorationProfileView>): void {
    const title = 'Profile (rfm-engine)';
    if (!res.ok)
      return void panels.profile.replaceChildren(
        el('h3', { text: title }),
        refusal(title, res.error, 'profile'),
      );
    const { profile, rule_set: ruleSet, rule_set_error: ruleSetError, window } = res.value;
    const parts: Node[] = [el('h3', { text: title })];
    if (profile.state === 'pending') {
      parts.push(
        testid(
          el('p', { cls: 'notice', text: 'No profile has been computed for this customer yet.' }),
          'profile-pending',
        ),
      );
      return void panels.profile.replaceChildren(...parts);
    }
    const score = (n: number | undefined): string => (n === undefined ? '' : ` (score ${n})`);
    const rows: [string, string, string][] = [
      ['State', profile.state, 'profile-state'],
      [
        'Rule set',
        ruleSet === undefined
          ? `#${profile.rule_set_id ?? '?'}`
          : `#${ruleSet.rule_set_id} ${ruleSet.name}${ruleSet.published ? ' (published)' : ''}`,
        'profile-rule-set',
      ],
      ['Window', window === undefined ? 'unknown' : windowText(window), 'profile-window'],
      ['Computed at', profile.computed_at ?? '', 'profile-computed-at'],
      [
        'Recency',
        profile.r_days === undefined
          ? 'no purchase in the window'
          : `${days(profile.r_days)}${score(profile.r_score)}`,
        'profile-recency',
      ],
      ['Frequency', `${profile.f_count ?? 0} purchases${score(profile.f_score)}`, 'profile-frequency'],
      ['Spend', `${profile.m_total ?? '0.00'}${score(profile.m_score)}`, 'profile-spend'],
    ];
    if (profile.label_code !== undefined)
      rows.push([
        'Label',
        `${profile.label_code}${profile.via_fallback === true ? ' (the fallback label: no band matched)' : ''}`,
        'profile-label',
      ]);
    if (profile.next_change_at !== undefined)
      rows.push(['Next change at', profile.next_change_at, 'profile-next-change']);
    parts.push(facts(rows));
    if (ruleSetError !== undefined)
      parts.push(
        testid(
          el('p', {
            cls: 'warning',
            text: 'The window of this profile could not be read, so the sales cannot be read over it.',
          }),
          'profile-window-unknown',
        ),
        refusal('Rule set', ruleSetError, 'rule-set'),
      );
    parts.push(
      el('p', {
        text: 'This is the label rfm-engine scored under its rule set. The customer’s current segment is segments-service’s, and is not shown here.',
      }),
    );
    panels.profile.replaceChildren(...parts);
  }

  // ---------- the figures across windows ----------

  function showFigures(
    profileResult: Result<ExplorationProfileView>,
    res: Result<ExplorationSalesView>,
  ): void {
    const title = 'Purchases, recency, frequency and spend';
    if (!res.ok)
      return void panels.figures.replaceChildren(
        el('h3', { text: title }),
        refusal('Sales of the customer', res.error, 'sales'),
      );
    const sales = res.value;
    const held = profileResult.ok ? profileResult.value : undefined;
    // A profile that is still pending holds no figure to set beside the sales.
    const scored = held !== undefined && held.profile.state !== 'pending' ? held.profile : undefined;
    const { compare, chosen } = sales;
    const same = scored !== undefined && compare !== undefined ? agreement(scored, compare) : undefined;
    const verdict = (agrees: boolean | undefined, says: string): string | undefined =>
      agrees === undefined ? undefined : agrees ? 'same as the profile' : `the profile says ${says}`;

    const profileWindow = (): string => {
      if (!profileResult.ok) return `${NOT_AVAILABLE}: the profile was refused (${profileResult.error.code})`;
      if (scored === undefined) return `${NOT_AVAILABLE}: no profile computed yet`;
      return held?.window === undefined ? `${NOT_AVAILABLE}: its window is unknown` : windowText(held.window);
    };
    const figure = (text: string, id: string, detail?: string[]): HTMLElement => {
      const td = cell(text, detail === undefined || detail.length === 0 ? undefined : detail.join('; '), id);
      td.classList.add('num');
      return td;
    };
    const salesColumn = (f: FiguresView | undefined, key: 'compare' | 'chosen'): HTMLElement[] => {
      if (f === undefined)
        return ['recency', 'frequency', 'spend'].map((n) => figure('—', `figure-${key}-${n}`));
      const against = key === 'compare';
      const latest =
        f.last_purchase_at === undefined
          ? []
          : [`latest purchase ${f.last_purchase_at}, counted to ${f.end}`];
      const check = (agrees: boolean | undefined, says: string): string[] => {
        const v = against ? verdict(agrees, says) : undefined;
        return v === undefined ? [] : [v];
      };
      return [
        figure(days(f.recency_days), `figure-${key}-recency`, [
          ...latest,
          ...check(same?.recency, scored?.r_days === undefined ? 'no purchase' : days(scored.r_days)),
        ]),
        figure(
          String(f.frequency),
          `figure-${key}-frequency`,
          check(same?.frequency, String(scored?.f_count ?? 0)),
        ),
        figure(f.spend, `figure-${key}-spend`, check(same?.spend, scored?.m_total ?? '0.00')),
      ];
    };
    const profileColumn = (): HTMLElement[] => [
      figure(
        scored === undefined
          ? '—'
          : scored.r_days === undefined
            ? 'no purchase in the window'
            : days(scored.r_days),
        'figure-profile-recency',
      ),
      figure(scored === undefined ? '—' : String(scored.f_count ?? 0), 'figure-profile-frequency'),
      figure(scored === undefined ? '—' : (scored.m_total ?? '0.00'), 'figure-profile-spend'),
    ];
    const row = (label: string, id: string, cells: HTMLElement[]): HTMLElement => {
      const th = el('th', { text: label });
      th.scope = 'row';
      return testid(el('tr', {}, th, ...cells), `figures-${id}`);
    };
    const [pr, pf, pm] = profileColumn() as [HTMLElement, HTMLElement, HTMLElement];
    const [cr, cf, cm] = salesColumn(compare, 'compare') as [HTMLElement, HTMLElement, HTMLElement];
    const [kr, kf, km] = salesColumn(chosen, 'chosen') as [HTMLElement, HTMLElement, HTMLElement];
    const table = el(
      'table',
      {},
      head([
        'Figure',
        'rfm-engine profile',
        'Sales over the profile’s window',
        'Sales over the chosen window',
      ]),
      el(
        'tbody',
        {},
        row('Window', 'window', [
          cell(profileWindow(), undefined, 'window-profile'),
          cell(
            compare === undefined
              ? `${NOT_AVAILABLE}: the profile’s window is not known`
              : windowText(compare.window),
            undefined,
            'window-compare',
          ),
          cell(windowText(chosen.window), undefined, 'window-chosen'),
        ]),
        row('Recency', 'recency', [pr, cr, kr]),
        row('Frequency (purchases)', 'frequency', [pf, cf, kf]),
        row('Spend', 'spend', [pm, cm, km]),
      ),
    );

    const parts: Node[] = [el('h3', { text: title })];
    if (sales.basis === 'net')
      parts.push(
        testid(
          el('p', {
            cls: 'notice',
            text: 'Sales are counted net of returns and cancellations (ADR-0062): a sale reversed in full is not a purchase, and spend is what remains.',
          }),
          'figures-net',
        ),
      );
    else {
      parts.push(
        testid(
          el('p', {
            cls: 'warning',
            text: 'The reversals of this customer could not be read, so the sales are counted as charged: a purchase that was returned or cancelled still counts. The profile of rfm-engine is net.',
          }),
          'figures-charged',
        ),
      );
      if (sales.reversals_error !== undefined)
        parts.push(refusal('Reversals of the customer’s sales', sales.reversals_error, 'reversals'));
    }
    if (sales.incomplete)
      parts.push(
        testid(
          el('p', {
            cls: 'warning',
            text: `Only the newest ${sales.sales_read} of the ${sales.sales_total} sales of this customer in these windows were read, so the figures of the oldest days are partial.`,
          }),
          'figures-incomplete',
        ),
      );
    parts.push(
      el('div', { cls: 'scroll' }, testid(table, 'figures-table')),
      el('p', {
        cls: 'detail',
        text: 'Recency counts whole days from the latest purchase in the window to the instant stated beside it. The profile’s figures are rfm-engine’s own; the sales columns are worked out here from the customer’s sales.',
      }),
    );
    panels.figures.replaceChildren(...parts);
  }

  // ---------- categories and channels ----------

  function showShares(res: Result<ExplorationSalesView>, kind: 'categories' | 'channels'): void {
    const panel = panels[kind];
    const title = kind === 'categories' ? 'Categories' : 'Channels';
    if (!res.ok)
      return void panel.replaceChildren(
        el('h3', { text: title }),
        refusal('Sales of the customer', res.error, kind),
      );
    const { chosen, compare } = res.value;
    const group = (text: string, span: number): HTMLElement => {
      const th = el('th', { text });
      th.colSpan = span;
      th.scope = 'colgroup';
      return th;
    };
    const first = el('tr', {}, el('th', { text: '' }));
    const second = el('tr');
    const heading = (text: string): HTMLElement => {
      const th = el('th', { text });
      th.scope = 'col';
      return th;
    };
    second.append(heading(kind === 'categories' ? 'Category' : 'Channel'));
    if (compare !== undefined) {
      first.append(
        testid(group(`Profile’s window: ${windowText(compare.window)}`, 2), `${kind}-window-compare`),
      );
      second.append(heading('Purchases'), heading('Spend'));
    }
    first.append(testid(group(`Chosen window: ${windowText(chosen.window)}`, 3), `${kind}-window-chosen`));
    second.append(heading('Purchases'), heading('Units'), heading('Spend'));

    // Every name either window has, the chosen window's first, in the order of their spend.
    const keys: (number | null)[] = [];
    for (const s of [...chosen[kind], ...(compare?.[kind] ?? [])]) if (!keys.includes(s.id)) keys.push(s.id);
    const find = (list: ShareView[] | undefined, id: number | null): ShareView | undefined =>
      list?.find((s) => s.id === id);
    const body = el('tbody');
    for (const id of keys) {
      const inChosen = find(chosen[kind], id);
      const inCompare = find(compare?.[kind], id);
      const tr = el('tr', {}, el('td', { text: (inChosen ?? inCompare)?.name ?? '' }));
      if (compare !== undefined)
        tr.append(
          el('td', { cls: 'num', text: String(inCompare?.purchases ?? 0) }),
          el('td', { cls: 'num', text: inCompare?.spend ?? '0.00' }),
        );
      tr.append(
        el('td', { cls: 'num', text: String(inChosen?.purchases ?? 0) }),
        el('td', { cls: 'num', text: String(inChosen?.units ?? 0) }),
        el('td', { cls: 'num', text: inChosen?.spend ?? '0.00' }),
      );
      tr.dataset.id = id === null ? 'none' : String(id);
      body.append(tr);
    }
    const parts: Node[] = [el('h3', { text: title })];
    if (kind === 'categories')
      parts.push(
        el('p', {
          text: 'Worked out from the lines of the customer’s sales and the category of each product. A purchase counts once in each category it includes, so the categories add up to more than the window’s purchases.',
        }),
      );
    else
      parts.push(
        el('p', { text: 'Worked out from the channel each of the customer’s sales was made through.' }),
      );
    if (kind === 'categories' && res.value.unknown_products > 0)
      parts.push(
        testid(
          el('p', {
            cls: 'warning',
            text: `${res.value.unknown_products} products could not be read, so their lines are listed under “No category known”.`,
          }),
          'categories-unknown',
        ),
        ...(res.value.categories_error === undefined ? [] : [errorBox(res.value.categories_error)]),
      );
    parts.push(
      keys.length === 0
        ? testid(
            el('p', {
              cls: 'notice',
              text: `No ${kind === 'categories' ? 'category' : 'channel'} in these windows: there is no purchase.`,
            }),
            `${kind}-empty`,
          )
        : el(
            'div',
            { cls: 'scroll' },
            testid(el('table', {}, el('thead', {}, first, second), body), `${kind}-table`),
          ),
    );
    panel.replaceChildren(...parts);
  }

  // ---------- the purchases ----------

  function showPurchases(res: Result<ExplorationSalesView>): void {
    const title = 'Purchases';
    if (!res.ok)
      return void panels.purchases.replaceChildren(
        el('h3', { text: title }),
        refusal('Sales of the customer', res.error, 'purchases'),
      );
    const { purchases, purchases_total: total, chosen } = res.value;
    const parts: Node[] = [
      el('h3', { text: title }),
      testid(
        el('p', { text: `Sales in the chosen window: ${windowText(chosen.window)}.` }),
        'purchases-window',
      ),
    ];
    if (purchases.length === 0) {
      parts.push(
        testid(el('p', { cls: 'notice', text: 'No sale in the chosen window.' }), 'purchases-empty'),
      );
      return void panels.purchases.replaceChildren(...parts);
    }
    const body = el('tbody');
    for (const s of purchases) {
      const tr = el(
        'tr',
        {},
        el('td', { text: `#${s.sale_id}` }),
        el('td', { text: s.occurred_at }),
        el('td', { text: s.store_name }),
        el('td', { text: s.channel_name }),
        el('td', { cls: 'num', text: s.charged }),
        el('td', { cls: 'num', text: s.net }),
        el('td', { text: STATUS[s.status] }),
      );
      tr.dataset.saleId = String(s.sale_id);
      tr.dataset.status = s.status;
      body.append(tr);
    }
    parts.push(
      el(
        'div',
        { cls: 'scroll' },
        testid(
          el('table', {}, head(['Sale', 'When', 'Store', 'Channel', 'Charged', 'Net', 'Counted']), body),
          'purchases-table',
        ),
      ),
    );
    if (total > purchases.length)
      parts.push(
        testid(
          el('p', {
            cls: 'notice',
            text: `The newest ${purchases.length} of ${total} sales are listed; all ${total} count in the figures.`,
          }),
          'purchases-more',
        ),
      );
    panels.purchases.replaceChildren(...parts);
  }
}
