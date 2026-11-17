import type { RfmBandView, RfmPreviewView, RfmRuleSetDraft, RfmRuleSetView } from '../shared/api';
import { blankDraft, deriveDraft, SCORES, validateDraft, type DraftIssue } from '../shared/rfm-rules';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, select, testid, textInput } from './dom';
import { latestRequest } from './latest-request';

// RFM configuration (F28-04, #873). A rule set is a window, four cut-points per measure, bands that map
// scores to labels and a fallback label. Stored rule sets are read-only, published or not: a change is a
// new rule set, derived from one (ADR-0064). A draft is edited here and checked as it is edited, each
// problem naming the band or the window that is wrong; its distribution of customers per label is shown
// before it is stored, and storing it publishes nothing (that is model-registry's decision).

const BAND_FIELDS = ['r_min', 'r_max', 'f_min', 'f_max', 'm_min', 'm_max'] as const;
const MEASURES = [
  [
    'recency_boundaries',
    'Recency (days)',
    'The largest recency that still earns the score; never decreasing from 5 to 2.',
  ],
  [
    'frequency_boundaries',
    'Frequency (purchases)',
    'The smallest purchase count that earns the score; never increasing.',
  ],
  [
    'monetary_boundaries',
    'Monetary (net total)',
    'The smallest net total that earns the score, as 450.00; never increasing.',
  ],
] as const;

const labelOf = (rule: RfmRuleSetView): string =>
  `#${rule.rule_set_id} ${rule.name}${rule.published ? ' (published)' : ''}`;

function table(headers: string[], rows: string[][], id: string): HTMLTableElement {
  const head = el('tr');
  for (const text of headers) {
    const th = el('th', { text });
    th.scope = 'col';
    head.append(th);
  }
  const body = el('tbody');
  for (const row of rows) body.append(el('tr', {}, ...row.map((text) => el('td', { text }))));
  return testid(el('table', {}, el('thead', {}, head), body), id);
}

/** A stored rule set as plain text in tables: nothing on it can be typed into. */
function readOnlyView(rule: RfmRuleSetView): HTMLElement {
  return testid(
    el(
      'section',
      {},
      el('h3', { text: labelOf(rule) }),
      testid(
        el('p', {
          cls: 'notice',
          text: rule.published
            ? 'This rule set is published, so it is read-only. A change is a new version: derive one and edit the draft.'
            : 'A stored rule set is read-only. A change is a new version: derive one and edit the draft.',
        }),
        'rfm-readonly-note',
      ),
      el('p', {
        text: `Window ${rule.window_days} days. Created ${rule.created_at}${rule.created_by === undefined ? ' by the system' : ` by ${rule.created_by}`}. Fallback label ${rule.fallback_label_code}.`,
      }),
      el(
        'div',
        { cls: 'scroll' },
        table(
          ['Measure', 'Score 5', 'Score 4', 'Score 3', 'Score 2'],
          [
            ['Recency (days, at most)', ...rule.recency_boundaries.map(String)],
            ['Frequency (purchases, at least)', ...rule.frequency_boundaries.map(String)],
            ['Monetary (net total, at least)', ...rule.monetary_boundaries],
          ],
          'rfm-opened-cutpoints',
        ),
      ),
      el(
        'div',
        { cls: 'scroll' },
        table(
          ['Label', 'R', 'F', 'M'],
          rule.bands.map((b) => [
            b.label_code,
            `${b.r_min} to ${b.r_max}`,
            `${b.f_min} to ${b.f_max}`,
            `${b.m_min} to ${b.m_max}`,
          ]),
          'rfm-opened-bands',
        ),
      ),
    ),
    'rfm-opened',
  );
}

function previewView(preview: RfmPreviewView): HTMLElement {
  const sameWindow = preview.basis.window_days === preview.draft_window_days;
  return testid(
    el(
      'section',
      {},
      el('h3', { text: 'Customers per label under this draft' }),
      testid(
        el('p', {
          text: `${preview.customers} customers scored under #${preview.basis.rule_set_id} ${preview.basis.name}, whose R, F and M cover ${preview.basis.window_days} days.`,
        }),
        'rfm-preview-basis',
      ),
      testid(
        el('p', {
          cls: 'notice',
          text: sameWindow
            ? 'The draft uses the same window, so these are the labels the draft would give those customers.'
            : `The draft's window is ${preview.draft_window_days} days, not ${preview.basis.window_days}. This preview scores the figures already held, so it shows the effect of the cut-points and the bands; a different window changes the figures themselves once the rule set is calculated.`,
        }),
        'rfm-preview-note',
      ),
      el(
        'div',
        { cls: 'scroll' },
        table(
          ['Label', 'Customers', 'Share', 'Only because no band matched'],
          preview.labels.map((l) => [
            l.label_code,
            String(l.customers),
            `${(Number(l.share) * 100).toFixed(1)}%`,
            String(l.via_fallback),
          ]),
          'rfm-preview-table',
        ),
      ),
    ),
    'rfm-preview',
  );
}

export function renderRfmRules(host: HTMLElement, ctx: ScreenContext): void {
  let rules: RfmRuleSetView[] = [];
  let draft: RfmRuleSetDraft = blankDraft();
  let preview: { key: string; view: RfmPreviewView } | null = null;
  const canStore = ctx.can('createRfmRuleSet');
  const canPreview = ctx.can('listRfmProfiles') && ctx.can('getRfmRuleSet');
  const latest = latestRequest();

  const message = el('div');
  const opened = el('div');
  const picker = testid(select([], ''), 'rfm-rule-set');
  const open = button('Open', 'rfm-open', () => void openSelected());
  const derive = testid(
    button('Derive a new version', 'rfm-derive', () => deriveFromOpened()),
    'rfm-derive',
  );
  derive.hidden = true;
  const editor = el('div');
  const issuesBox = el('div');
  const basis = testid(select([], ''), 'rfm-basis');
  const previewButton = button('Preview the distribution', 'rfm-preview-run', () => void runPreview());
  const previewArea = el('div');
  const saveButton = button('Store as a new rule set', 'rfm-save', () => void save());
  const saveMessage = el('div');
  let openedRule: RfmRuleSetView | null = null;

  const keyOf = (): string => JSON.stringify([draft, basis.value]);
  const issues = (): DraftIssue[] => validateDraft(draft);

  function refresh(): void {
    const found = issues();
    // A preview stays only while it describes the draft on screen.
    if (preview !== null && preview.key !== keyOf()) {
      preview = null;
      previewArea.replaceChildren();
    }
    issuesBox.replaceChildren(
      found.length === 0
        ? testid(el('p', { cls: 'notice', text: 'No problems found in this draft.' }), 'rfm-valid')
        : testid(
            el(
              'div',
              {},
              el('p', {
                text: `${found.length} ${found.length === 1 ? 'problem' : 'problems'} to correct before this draft can be previewed or stored:`,
              }),
              el(
                'ul',
                {},
                ...found.map((i) => {
                  const li = testid(el('li', { cls: 'error', text: i.message }), 'rfm-issue');
                  li.dataset.field = i.field;
                  li.dataset.code = i.code;
                  return li;
                }),
              ),
            ),
            'rfm-issues',
          ),
    );
    editor.querySelectorAll<HTMLElement>('input[data-field]').forEach((control) => {
      const bad = found.some((i) => i.field === control.dataset.field);
      control.setAttribute('aria-invalid', String(bad));
    });
    previewButton.disabled = found.length > 0 || !canPreview || basis.value === '';
    saveButton.disabled = found.length > 0 || preview === null;
    if (!canStore) saveButton.hidden = true;
  }

  function numberInput(field: string, value: number | string, set: (v: string) => void): HTMLInputElement {
    const input = textInput(String(value), 'text');
    input.inputMode = 'decimal';
    input.dataset.field = field;
    input.setAttribute('aria-label', field);
    input.addEventListener('input', () => {
      set(input.value);
      refresh();
    });
    return input;
  }

  const whole = (text: string): number => (/^-?[0-9]+$/.test(text.trim()) ? Number(text.trim()) : Number.NaN);

  function drawEditor(): void {
    const name = textInput(draft.name);
    name.dataset.field = 'name';
    name.addEventListener('input', () => {
      draft.name = name.value;
      refresh();
    });
    const windowDays = numberInput('window_days', draft.window_days, (v) => {
      draft.window_days = whole(v);
    });
    const fallback = textInput(draft.fallback_label_code);
    fallback.dataset.field = 'fallback_label_code';
    fallback.addEventListener('input', () => {
      draft.fallback_label_code = fallback.value;
      refresh();
    });

    const cutRows = MEASURES.map(([key, title, help]) => {
      const cells = SCORES.map((score, i) => {
        const input = numberInput(`${key}[${i}]`, draft[key][i] as number | string, (v) => {
          if (key === 'monetary_boundaries') draft.monetary_boundaries[i] = v.trim();
          else draft[key][i] = whole(v);
        });
        input.id = `rfm-${key}-${i}`;
        const label = el('label', { text: `Score ${score}` });
        label.setAttribute('for', input.id);
        return el('div', { cls: 'form-field' }, label, input);
      });
      return el('fieldset', {}, el('legend', { text: title }), el('p', { text: help }), ...cells);
    });

    const bandRows = draft.bands.map((band, index) => {
      const label = textInput(band.label_code);
      label.dataset.field = `bands[${index}].label_code`;
      label.setAttribute('aria-label', `Band ${index + 1} label`);
      label.addEventListener('input', () => {
        band.label_code = label.value;
        refresh();
      });
      const scores = BAND_FIELDS.map((f) => {
        const input = numberInput(`bands[${index}].${f}`, band[f], (v) => {
          band[f] = whole(v);
        });
        input.setAttribute('aria-label', `Band ${index + 1} ${f}`);
        return el('td', {}, input);
      });
      const remove = button('Remove', `rfm-band-remove-${index}`, () => {
        draft.bands.splice(index, 1);
        drawEditor();
        refresh();
      });
      return el(
        'tr',
        {},
        el('td', { text: String(index + 1) }),
        el('td', {}, label),
        ...scores,
        el('td', {}, remove),
      );
    });
    const head = el('tr');
    for (const t of ['Band', 'Label', 'R min', 'R max', 'F min', 'F max', 'M min', 'M max', '']) {
      const th = el('th', { text: t });
      th.scope = 'col';
      head.append(th);
    }
    const addBand = button('Add a band', 'rfm-band-add', () => {
      const fresh: RfmBandView = {
        label_code: '',
        r_min: 1,
        r_max: 5,
        f_min: 1,
        f_max: 5,
        m_min: 1,
        m_max: 5,
      };
      draft.bands.push(fresh);
      drawEditor();
      refresh();
    });
    const field = (title: string, control: HTMLElement, id: string): HTMLElement => {
      control.id = id;
      const lab = el('label', { text: title });
      lab.setAttribute('for', id);
      return el('div', { cls: 'form-field' }, lab, control);
    };
    editor.replaceChildren(
      testid(
        el(
          'form',
          {},
          field('Name', name, 'rfm-name'),
          field('Window (days, 1 to 3650)', windowDays, 'rfm-window'),
          ...cutRows,
          el('h3', { text: 'Bands' }),
          el('p', {
            text: 'A band takes a label for the customers whose three scores fall inside its box. Bands must not overlap; a customer in no band takes the fallback label.',
          }),
          el(
            'div',
            { cls: 'scroll' },
            testid(el('table', {}, el('thead', {}, head), el('tbody', {}, ...bandRows)), 'rfm-bands'),
          ),
          addBand,
          field('Fallback label', fallback, 'rfm-fallback'),
        ),
        'rfm-draft',
      ),
    );
    editor.querySelector('form')?.addEventListener('submit', (e) => e.preventDefault());
  }

  async function openSelected(): Promise<void> {
    message.replaceChildren();
    const chosen = Number(picker.value);
    const found = rules.find((r) => r.rule_set_id === chosen);
    if (found === undefined) return;
    await latest(
      () => ctx.api.getRfmRuleSet(chosen),
      (res) => {
        if (!res.ok) {
          opened.replaceChildren(errorBox(res.error));
          derive.hidden = true;
          return;
        }
        openedRule = res.value;
        opened.replaceChildren(readOnlyView(res.value));
        derive.hidden = false;
      },
    );
  }

  function deriveFromOpened(): void {
    if (openedRule === null) return;
    draft = deriveDraft(openedRule);
    preview = null;
    previewArea.replaceChildren();
    saveMessage.replaceChildren();
    drawEditor();
    refresh();
    message.replaceChildren(
      testid(
        el('p', {
          cls: 'notice',
          text: `A draft was started from #${openedRule.rule_set_id} ${openedRule.name}. The stored rule set is unchanged.`,
        }),
        'rfm-derived',
      ),
    );
  }

  async function runPreview(): Promise<void> {
    saveMessage.replaceChildren();
    const key = keyOf();
    const sent = structuredClone(draft);
    previewArea.replaceChildren(el('p', { text: 'Loading the customers…' }));
    await latest(
      () => ctx.api.previewRfmRuleSet(sent, Number(basis.value)),
      (res) => {
        if (!res.ok) {
          previewArea.replaceChildren(errorBox(res.error));
          return;
        }
        preview = { key, view: res.value };
        previewArea.replaceChildren(previewView(res.value));
        refresh();
      },
    );
  }

  async function save(): Promise<void> {
    if (preview === null) return;
    saveMessage.replaceChildren();
    await latest(
      () => ctx.api.createRfmRuleSet(structuredClone(draft)),
      (res) => {
        if (!res.ok) {
          saveMessage.replaceChildren(errorBox(res.error));
          return;
        }
        saveMessage.replaceChildren(
          testid(
            el('p', {
              cls: 'notice',
              text: `Stored as rule set #${res.value.rule_set_id}. It is not published: publication is decided in model-registry.`,
            }),
            'rfm-saved',
          ),
        );
        void loadList();
      },
    );
  }

  async function loadList(): Promise<void> {
    const res = await ctx.api.listRfmRuleSets({ page: 1, size: 100 });
    if (!res.ok) return void message.replaceChildren(errorBox(res.error));
    rules = res.value.items;
    const options: [string, string][] = rules.map((r) => [String(r.rule_set_id), labelOf(r)]);
    const fill = (control: HTMLSelectElement): void => {
      control.replaceChildren(
        ...options.map(([value, text]) => {
          const option = el('option', { text });
          option.value = value;
          return option;
        }),
      );
    };
    const previous = basis.value;
    fill(picker);
    fill(basis);
    basis.value = options.some(([v]) => v === previous)
      ? previous
      : (rules.find((r) => r.published)?.rule_set_id.toString() ?? options[0]?.[0] ?? '');
    open.disabled = options.length === 0;
    refresh();
  }

  host.replaceChildren(
    el('h1', { text: 'RFM rules' }),
    el('p', {
      text: 'A rule set holds the window, the cut-points that turn recency, frequency and spend into scores, and the bands that turn scores into labels. Stored rule sets never change; a change is a new rule set.',
    }),
    el('h2', { text: 'Stored rule sets' }),
    el('div', { cls: 'form-field' }, el('label', { text: 'Rule set' }), picker),
    open,
    derive,
    message,
    opened,
    el('h2', { text: 'Draft' }),
    el('p', {
      text: 'The draft lives on this screen until it is stored. Nothing is sent while it is edited.',
    }),
    editor,
    issuesBox,
    el('h2', { text: 'Preview' }),
    el('div', { cls: 'form-field' }, el('label', { text: 'Preview on the customers scored under' }), basis),
    previewButton,
    previewArea,
    ...(canStore ? [saveButton, saveMessage] : []),
  );
  drawEditor();
  refresh();
  void loadList();
}
