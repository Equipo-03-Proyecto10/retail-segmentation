import type { CampaignStatus, CampaignView, LabelView } from '../shared/api';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, formField, markDetails, pager, select, show, testid, textInput } from './dom';
import { latestRequest } from './latest-request';

// Campaigns and their transitions (F16-08, #486). ADMIN, ANALYST, MARKETING and
// AUDITOR read; ADMIN and MARKETING write (x-mosaiq-roles, through ctx.can).
// The lifecycle is campaigns-service's: the buttons offered are the moves it
// returned in allowed_transitions, and every refusal is shown as returned.

const FORM_FIELDS = ['name', 'label_code', 'starts_on', 'ends_on'];
const STATUSES: CampaignStatus[] = ['DRAFT', 'ACTIVE', 'FINISHED', 'CANCELLED'];

const SIZES: [string, string][] = [
  ['20', '20 a page'],
  ['50', '50 a page'],
  ['100', '100 a page'],
];

export function renderCampaigns(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const message = testid(el('div'), 'campaigns-message');
  const tableArea = el('div');
  const detail = testid(el('section', { cls: 'panel' }), 'campaign-detail');
  const renderLatestList = latestRequest();
  const renderLatestDetail = latestRequest();
  const renderLatestLabels = latestRequest();
  let labels: LabelView[] = [];
  let query: { status?: CampaignStatus; label_code?: string; page: number; size: number } = {
    page: 1,
    size: 20,
  };

  /** Never rejects: a vocabulary that cannot be read shows its error and the screen still loads. */
  const labelsReady: Promise<void> = renderLatestLabels(
    () => api.listCampaignLabels(),
    (res) => {
      if (!res.ok) return void message.replaceChildren(errorBox(res.error));
      labels = res.value;
      fillLabelOptions(labelFilter, 'All labels');
      fillLabelOptions(createLabel, null);
    },
  ).catch(() => undefined);

  const labelName = (code: string): string => {
    const found = labels.find((l) => l.label_code === code);
    return found === undefined ? code : `${found.name} (${code})`;
  };

  function fillLabelOptions(control: HTMLSelectElement, empty: string | null): void {
    const keep = control.value;
    control.replaceChildren();
    const options: [string, string][] = labels.map((l) => [l.label_code, `${l.name} (${l.label_code})`]);
    if (empty !== null) options.unshift(['', empty]);
    for (const [value, text] of options) {
      const o = el('option', { text });
      o.value = value;
      control.append(o);
    }
    if (options.some(([value]) => value === keep)) control.value = keep;
  }

  const status = select([['', 'Any status'], ...STATUSES.map((s): [string, string] => [s, s])], '');
  const labelFilter = select([['', 'All labels']], '');
  const size = select(SIZES, '20');
  const search = el('button', { text: 'Search' });
  search.type = 'submit';
  const filters = testid(
    el(
      'form',
      { cls: 'filters' },
      formField('status', 'Status', status, [], 'filter'),
      formField('label_code', 'Label', labelFilter, [], 'filter'),
      formField('size', 'Page size', size, [], 'filter'),
      search,
    ),
    'campaign-filters',
  );
  filters.noValidate = true;
  filters.addEventListener('submit', (event) => {
    event.preventDefault();
    query = { page: 1, size: Number(size.value) };
    if (status.value) query.status = status.value as CampaignStatus;
    if (labelFilter.value) query.label_code = labelFilter.value;
    void load();
  });

  async function load(): Promise<void> {
    await labelsReady;
    await renderLatestList(
      () => api.listCampaigns(query),
      (res) => {
        if (!res.ok) return void tableArea.replaceChildren(errorBox(res.error));
        const head = el('tr');
        for (const h of ['ID', 'Name', 'Label', 'Starts', 'Ends', 'Status', ''])
          head.append(el('th', { text: h }));
        const body = el('tbody');
        for (const c of res.value.items) {
          const row = el(
            'tr',
            {},
            el('td', { text: String(c.campaign_id) }),
            el('td', { text: c.name }),
            testid(el('td', { text: labelName(c.label_code) }), `campaign-label-${c.campaign_id}`),
            el('td', { text: c.starts_on }),
            el('td', { text: c.ends_on }),
            testid(el('td', { text: c.status }), `campaign-status-${c.campaign_id}`),
            el(
              'td',
              {},
              button('View', `view-campaign-${c.campaign_id}`, () => void open(c.campaign_id)),
            ),
          );
          row.dataset.campaignId = String(c.campaign_id);
          body.append(row);
        }
        tableArea.replaceChildren(
          testid(el('table', {}, el('thead', {}, head), body), 'campaigns-table'),
          pager(res.value.page, 'campaigns', (page) => {
            query = { ...query, page };
            void load();
          }),
        );
      },
    );
  }

  async function open(campaignId: number): Promise<void> {
    await labelsReady;
    await renderLatestDetail(
      () => api.getCampaign(campaignId),
      (res) => {
        if (!res.ok) return void detail.replaceChildren(errorBox(res.error));
        showCampaign(res.value);
      },
    );
  }

  function showCampaign(c: CampaignView): void {
    const facts = el('dl');
    const fact = (name: string, value: string, id: string): void => {
      facts.append(el('dt', { text: name }), testid(el('dd', { text: value }), id));
    };
    fact('Name', c.name, 'campaign-name');
    fact('Label', labelName(c.label_code), 'campaign-label');
    fact('Starts on', c.starts_on, 'campaign-starts-on');
    fact('Ends on', c.ends_on, 'campaign-ends-on');
    fact('Status', c.status, 'campaign-status');
    const parts: Node[] = [el('h3', { text: `${c.name} (#${c.campaign_id})` }), facts];
    // ANALYST and AUDITOR read only: no control is drawn for them.
    if (ctx.can('updateCampaign')) parts.push(editForm(c));
    if (ctx.can('transitionCampaign')) parts.push(transitions(c));
    detail.replaceChildren(...parts);
  }

  function editForm(c: CampaignView): HTMLElement {
    const draft = c.status === 'DRAFT';
    const name = textInput(c.name);
    const label = select(
      [
        ...(labels.some((l) => l.label_code === c.label_code)
          ? []
          : ([[c.label_code, c.label_code]] as [string, string][])),
        ...labels.map((l): [string, string] => [l.label_code, `${l.name} (${l.label_code})`]),
      ],
      c.label_code,
    );
    const starts = textInput(c.starts_on, 'date');
    const ends = textInput(c.ends_on, 'date');
    const save = el('button', { text: 'Save changes' });
    save.type = 'submit';
    const controls = [name, label, starts, ends, save];
    const form = testid(
      el(
        'form',
        {},
        el('h4', { text: 'Edit the campaign' }),
        formField('name', 'Name', name, [], 'edit'),
        formField('label_code', 'Label', label, [], 'edit'),
        formField('starts_on', 'Starts on', starts, [], 'edit'),
        formField('ends_on', 'Ends on', ends, [], 'edit'),
        save,
      ),
      'campaign-edit-form',
    );
    form.noValidate = true;
    if (!draft) {
      for (const control of controls) control.disabled = true;
      form.append(
        testid(
          el('p', { text: `A ${c.status} campaign cannot be edited: only a DRAFT can.` }),
          'edit-locked',
        ),
      );
    }
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const changes: Record<string, string> = {};
      if (name.value !== c.name) changes.name = name.value;
      if (label.value !== c.label_code) changes.label_code = label.value;
      if (starts.value !== c.starts_on) changes.starts_on = starts.value;
      if (ends.value !== c.ends_on) changes.ends_on = ends.value;
      const isCurrent = renderLatestDetail.begin();
      void api.updateCampaign(c.campaign_id, changes).then(async (res) => {
        if (!isCurrent()) return;
        show(message, res, res.ok ? `Campaign ${res.value.name} saved.` : '', FORM_FIELDS);
        if (res.ok) showCampaign(res.value);
        else markDetails(form, res.error.details);
        await load();
      });
    });
    return form;
  }

  function transitions(c: CampaignView): HTMLElement {
    const area = testid(el('div', { cls: 'transitions' }), 'campaign-transitions');
    if (c.allowed_transitions.length === 0)
      area.append(el('p', { text: `A ${c.status} campaign cannot move to another status.` }));
    for (const to of c.allowed_transitions) {
      area.append(
        button(`Move to ${to}`, `transition-${to}`, () => {
          const isCurrent = renderLatestDetail.begin();
          // expected_status is the status this screen displayed.
          void api.transitionCampaign(c.campaign_id, to, c.status).then(async (res) => {
            if (!isCurrent()) return;
            show(message, res, res.ok ? `Campaign ${res.value.name} is now ${res.value.status}.` : '');
            if (res.ok) showCampaign(res.value);
            // A stale status is never retried: read the campaign as it stands.
            else if (res.error.code === 'status_changed') await open(c.campaign_id);
            await load();
          });
        }),
      );
    }
    return area;
  }

  const createLabel = select([], '');
  function createForm(): HTMLElement {
    const name = textInput('');
    const starts = textInput('', 'date');
    const ends = textInput('', 'date');
    const save = el('button', { text: 'Create campaign' });
    save.type = 'submit';
    const form = testid(
      el(
        'form',
        { cls: 'panel' },
        el('h3', { text: 'New campaign' }),
        formField('name', 'Name', name, [], 'create'),
        formField('label_code', 'Label', createLabel, [], 'create'),
        formField('starts_on', 'Starts on', starts, [], 'create'),
        formField('ends_on', 'Ends on', ends, [], 'create'),
        save,
      ),
      'campaign-create-form',
    );
    form.noValidate = true;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const isCurrent = renderLatestDetail.begin();
      void api
        .createCampaign({
          name: name.value,
          label_code: createLabel.value,
          starts_on: starts.value,
          ends_on: ends.value,
        })
        .then(async (res) => {
          if (!isCurrent()) return;
          show(
            message,
            res,
            res.ok ? `Campaign ${res.value.name} created as ${res.value.status}.` : '',
            FORM_FIELDS,
          );
          if (res.ok) {
            form.reset();
            showCampaign(res.value);
          } else markDetails(form, res.error.details);
          await load();
        });
    });
    return form;
  }

  const parts: Node[] = [el('h2', { text: 'Campaigns' })];
  if (ctx.can('createCampaign')) parts.push(createForm());
  parts.push(filters, message, tableArea, detail);
  host.replaceChildren(...parts);
  void load();
}
