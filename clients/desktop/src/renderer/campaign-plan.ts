import type {
  AudienceBreakdownView,
  AudiencePreviewView,
  CampaignView,
  ExclusionsView,
  LabelView,
  Result,
} from '../shared/api';
import { leftOut, plainDetail, reasonsOf, reasonText } from '../shared/campaign-messages';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, formField, markDetails, show, testid, textInput } from './dom';
import { latestRequest } from './latest-request';

// A campaign's plan on the campaigns screen (F28-09, #878): audience, conditions, exclusions, a
// preview of who that reaches, and budget. ADMIN and MARKETING write; ANALYST and AUDITOR read
// (x-mosaiq-roles, through ctx.can). Each section reads and saves on its own, so one that fails shows
// its refusal in place and the others stay usable. Only a DRAFT can be changed: outside it every
// control is drawn disabled, and a forced write is answered 409 campaign_not_draft by the service.
// A campaign's validity is the campaign's own dates, edited in the form above this plan.

interface PlanScreen {
  ctx: ScreenContext;
  labels: LabelView[];
  labelName(code: string): string;
}

/** How a section announces a save, redraws itself with what the service stored, and drops stale answers. */
interface Saver<T> {
  result: HTMLElement;
  begin(): () => boolean;
  redraw(value: T): void;
}

type Control = HTMLInputElement | HTMLTextAreaElement | HTMLButtonElement;

function facts(rows: [name: string, value: string, id: string][]): HTMLElement {
  const list = el('dl');
  for (const [name, value, id] of rows)
    list.append(el('dt', { text: name }), testid(el('dd', { text: value }), id));
  return list;
}

function submitButton(text: string, id: string): HTMLButtonElement {
  const b = testid(el('button', { text }), id);
  b.type = 'submit';
  return b;
}

/** A section that reads when it is drawn, shows a refusal of the read in place, and redraws after a save. */
function planSection<T>(
  id: string,
  title: string,
  hint: string,
  read: () => Promise<Result<T>>,
  draw: (value: T, saver: Saver<T>) => Node[],
): HTMLElement {
  const body = el('div');
  const result = testid(el('div'), `plan-${id}-result`);
  const latest = latestRequest();
  const saver: Saver<T> = {
    result,
    begin: latest.begin,
    redraw: (value) => body.replaceChildren(...draw(value, saver)),
  };
  const load = (): Promise<void> =>
    latest(read, (res) => {
      if (res.ok) return saver.redraw(res.value);
      body.replaceChildren(
        errorBox(res.error),
        button('Reload', `plan-${id}-reload`, () => void load()),
      );
    });
  body.replaceChildren(el('p', { cls: 'busy', text: 'Loading…' }));
  void load();
  return testid(
    el('section', { cls: 'panel' }, el('h4', { text: title }), el('p', { text: hint }), body, result),
    `plan-${id}`,
  );
}

/** Saves one section: its own refusal shows in plain words next to the field, and what was typed stays. */
function save<T>(
  saver: Saver<T>,
  form: HTMLFormElement,
  write: () => Promise<Result<T>>,
  saved: string,
  fields: string[],
  after: () => void,
): void {
  const isCurrent = saver.begin();
  void write().then((res) => {
    if (!isCurrent()) return;
    show(saver.result, res, res.ok ? saved : '', fields);
    if (res.ok) {
      saver.redraw(res.value);
      after();
    } else markDetails(form, res.error.details, plainDetail);
  });
}

export function renderPlan(screen: PlanScreen, c: CampaignView): HTMLElement {
  const { ctx } = screen;
  const { api } = ctx;
  const locked = c.status !== 'DRAFT';
  let previewShown = false;
  const refreshPreview: { run: () => void } = { run: () => undefined };
  const afterSave = (): void => {
    if (previewShown) refreshPreview.run();
  };

  /** The controls a role cannot use, or a campaign outside DRAFT cannot take, are drawn disabled. */
  const lock = (controls: Control[]): void => {
    for (const control of controls) control.disabled = locked;
  };

  const audience = (): HTMLElement =>
    planSection(
      'audience',
      'Audience',
      'Who the campaign targets: the customers whose current segment label is the campaign label. A cap cuts the audience to that many customers.',
      () => api.getCampaignAudience(c.campaign_id),
      (a, saver) => {
        const label: [string, string, string] = ['Label', screen.labelName(a.label_code), 'audience-label'];
        if (!ctx.can('putCampaignAudience'))
          return [
            facts([
              label,
              ['Cap', a.max_customers === undefined ? 'No cap' : String(a.max_customers), 'audience-cap'],
            ]),
          ];
        const max = textInput(a.max_customers === undefined ? '' : String(a.max_customers));
        max.inputMode = 'numeric';
        const submit = submitButton('Save audience', 'audience-save');
        lock([max, submit]);
        const form = testid(
          el(
            'form',
            {},
            facts([label]),
            formField('max_customers', 'Maximum number of customers (empty for no cap)', max, [], 'audience'),
            submit,
          ),
          'audience-form',
        );
        form.noValidate = true;
        form.addEventListener('submit', (event) => {
          event.preventDefault();
          save(
            saver,
            form,
            () => api.putCampaignAudience(c.campaign_id, { max_customers: max.value }),
            'Audience saved.',
            ['max_customers'],
            afterSave,
          );
        });
        return [form];
      },
    );

  const conditions = (): HTMLElement =>
    planSection(
      'conditions',
      'Conditions',
      'What a customer in the audience must also satisfy. Every condition that is set must hold. Purchases are counted net of returns.',
      () => api.getCampaignEligibilityRules(c.campaign_id),
      (r, saver) => {
        const registered = r.registered_on_or_before ?? '';
        const min = r.min_purchases === undefined ? '' : String(r.min_purchases);
        const days = r.purchase_window_days === undefined ? '' : String(r.purchase_window_days);
        if (!ctx.can('putCampaignEligibilityRules'))
          return [
            facts([
              [
                'Registered on or before',
                registered === '' ? 'Any date' : registered,
                'conditions-registered',
              ],
              [
                'Purchases',
                min === '' || days === ''
                  ? 'No purchase condition'
                  : `At least ${min} in the ${days} days before the request`,
                'conditions-purchases',
              ],
            ]),
          ];
        const registeredInput = textInput(registered, 'date');
        const minInput = textInput(min);
        minInput.inputMode = 'numeric';
        const daysInput = textInput(days);
        daysInput.inputMode = 'numeric';
        const submit = submitButton('Save conditions', 'conditions-save');
        lock([registeredInput, minInput, daysInput, submit]);
        const form = testid(
          el(
            'form',
            {},
            formField(
              'registered_on_or_before',
              'Registered on or before (empty for any date)',
              registeredInput,
              [],
              'conditions',
            ),
            formField(
              'min_purchases',
              'At least this many purchases (1 to 1000, empty for none)',
              minInput,
              [],
              'conditions',
            ),
            formField(
              'purchase_window_days',
              'in the last this many days (1 to 731, given together with the purchases)',
              daysInput,
              [],
              'conditions',
            ),
            submit,
          ),
          'conditions-form',
        );
        form.noValidate = true;
        form.addEventListener('submit', (event) => {
          event.preventDefault();
          save(
            saver,
            form,
            () =>
              api.putCampaignEligibilityRules(c.campaign_id, {
                registered_on_or_before: registeredInput.value,
                min_purchases: minInput.value,
                purchase_window_days: daysInput.value,
              }),
            'Conditions saved.',
            ['registered_on_or_before', 'min_purchases', 'purchase_window_days'],
            afterSave,
          );
        });
        return [form];
      },
    );

  const exclusions = (): HTMLElement =>
    planSection(
      'exclusions',
      'Exclusions',
      'Customers who are never reached: named by id, or every holder of a segment label.',
      () => api.getCampaignExclusions(c.campaign_id),
      (x, saver) => {
        if (!ctx.can('putCampaignExclusions')) return [exclusionFacts(screen, x)];
        const ids = el('textarea');
        ids.value = x.customer_ids.join('\n');
        const choices = el('div', { cls: 'choices' });
        choices.setAttribute('name', 'label_codes');
        const boxes: [string, HTMLInputElement][] = [];
        // A stored label the vocabulary no longer lists is still offered, so saving never drops it unseen.
        const codes = [
          ...screen.labels.map((l) => l.label_code),
          ...x.label_codes.filter((code) => !screen.labels.some((l) => l.label_code === code)),
        ];
        for (const code of codes) {
          const box = el('input');
          box.type = 'checkbox';
          box.checked = x.label_codes.includes(code);
          box.dataset.testid = `exclusion-label-${code}`;
          boxes.push([code, box]);
          choices.append(el('label', {}, box, el('span', { text: screen.labelName(code) })));
        }
        const submit = submitButton('Save exclusions', 'exclusions-save');
        lock([ids, ...boxes.map(([, box]) => box), submit]);
        const labels = el('fieldset', {}, el('legend', { text: 'Excluded labels (up to 20)' }), choices);
        const form = testid(
          el(
            'form',
            {},
            formField(
              'customer_ids',
              'Customer ids to exclude, one per line (up to 1000)',
              ids,
              [],
              'exclusions',
            ),
            labels,
            submit,
          ),
          'exclusions-form',
        );
        form.noValidate = true;
        form.addEventListener('submit', (event) => {
          event.preventDefault();
          save(
            saver,
            form,
            () =>
              api.putCampaignExclusions(c.campaign_id, {
                customer_ids: ids.value.split(/[\s,;]+/).filter((v) => v !== ''),
                label_codes: boxes.filter(([, box]) => box.checked).map(([code]) => code),
              }),
            'Exclusions saved.',
            ['customer_ids', 'label_codes'],
            afterSave,
          );
        });
        return [form];
      },
    );

  const budget = (): HTMLElement =>
    planSection(
      'budget',
      'Budget',
      'The most the campaign may grant in discounts, and the most one customer may receive. Amounts have two decimals; no currency is recorded.',
      () => api.getCampaignBudget(c.campaign_id),
      (b, saver) => {
        const total = b.total_amount ?? '';
        const cap = b.per_customer_cap ?? '';
        if (!ctx.can('putCampaignBudget'))
          return [
            facts([
              ['Total', total === '' ? 'No limit' : total, 'budget-total'],
              ['Per customer', cap === '' ? 'No limit' : cap, 'budget-per-customer'],
            ]),
          ];
        const totalInput = textInput(total);
        totalInput.inputMode = 'decimal';
        const capInput = textInput(cap);
        capInput.inputMode = 'decimal';
        const submit = submitButton('Save budget', 'budget-save');
        lock([totalInput, capInput, submit]);
        const form = testid(
          el(
            'form',
            {},
            formField(
              'total_amount',
              'Total budget, for example 150000.00 (empty for no limit)',
              totalInput,
              [],
              'budget',
            ),
            formField(
              'per_customer_cap',
              'Per-customer limit, for example 300.00 (empty for no limit)',
              capInput,
              [],
              'budget',
            ),
            submit,
          ),
          'budget-form',
        );
        form.noValidate = true;
        form.addEventListener('submit', (event) => {
          event.preventDefault();
          save(
            saver,
            form,
            () =>
              api.putCampaignBudget(c.campaign_id, {
                total_amount: totalInput.value,
                per_customer_cap: capInput.value,
              }),
            'Budget saved.',
            ['total_amount', 'per_customer_cap'],
            afterSave,
          );
        });
        return [form];
      },
    );

  const preview = (): HTMLElement =>
    previewSection(screen, c, refreshPreview, () => {
      previewShown = true;
    });

  const parts: Node[] = [el('h3', { text: 'Audience and limits' })];
  if (locked)
    parts.push(
      testid(
        el('p', { text: `A ${c.status} campaign's plan cannot be changed: only a DRAFT can.` }),
        'plan-locked',
      ),
    );
  // A section is drawn, and read, only for a role that may read it.
  const reads = (...operations: string[]): boolean => operations.every((op) => ctx.can(op));
  if (reads('getCampaignAudience')) parts.push(audience());
  if (reads('getCampaignEligibilityRules')) parts.push(conditions());
  if (reads('getCampaignExclusions')) parts.push(exclusions());
  if (reads('getCampaignAudience', 'getCampaignEligibilityRules', 'getCampaignExclusions'))
    parts.push(preview());
  if (reads('getCampaignBudget')) parts.push(budget());
  return testid(el('section', { cls: 'panel' }, ...parts), 'campaign-plan');
}

/** What a role that may not write sees of the exclusions. */
function exclusionFacts(screen: PlanScreen, x: ExclusionsView): HTMLElement {
  const wrap = el('div');
  const ids = el('ul');
  for (const id of x.customer_ids) ids.append(el('li', { text: id }));
  wrap.append(
    el('h5', { text: 'Customers' }),
    x.customer_ids.length === 0
      ? testid(el('p', { text: 'None' }), 'exclusions-customers')
      : testid(ids, 'exclusions-customers'),
    facts([
      [
        'Labels',
        x.label_codes.length === 0 ? 'None' : x.label_codes.map((code) => screen.labelName(code)).join(', '),
        'exclusions-labels',
      ],
    ]),
  );
  return wrap;
}

/**
 * The audience as campaigns-service counts it now, and why it is that size. It reads what is saved,
 * not what is typed, so a section is saved before it is previewed. After a save, a preview already on
 * screen is read again.
 */
function previewSection(
  screen: PlanScreen,
  c: CampaignView,
  refresh: { run: () => void },
  shown: () => void,
): HTMLElement {
  const { api } = screen.ctx;
  const area = testid(el('div'), 'preview-result');
  const latest = latestRequest();
  const run = (): void => {
    shown();
    area.replaceChildren(el('p', { cls: 'busy', text: 'Loading…' }));
    void latest(
      () => api.previewCampaignAudience(c.campaign_id),
      (res) => {
        if (!res.ok) return void area.replaceChildren(errorBox(res.error));
        area.replaceChildren(previewOf(screen, res.value));
      },
    );
  };
  refresh.run = run;
  return testid(
    el(
      'section',
      { cls: 'panel' },
      el('h4', { text: 'Audience preview' }),
      el('p', {
        text: 'Counts the audience with what is saved now: save a section first to see its effect. The count is an estimate as of the moment shown.',
      }),
      button('Preview audience', 'preview-run', run),
      area,
    ),
    'plan-preview',
  );
}

function previewOf(screen: PlanScreen, p: AudiencePreviewView): HTMLElement {
  return p.breakdown === undefined ? totalOnly(screen, p) : byReason(screen, p, p.breakdown);
}

/** The label's name with its code, for a row that is about the campaign's label. */
const holdersName = (screen: PlanScreen, code: string): string =>
  `Customers holding ${screen.labelName(code)}`;

/** The audience with its exclusions counted by reason, each customer under the first reason that applies. */
function byReason(screen: PlanScreen, p: AudiencePreviewView, b: AudienceBreakdownView): HTMLElement {
  const wrap = el('div');
  wrap.append(
    facts([
      ['Estimated audience', `${b.audience_size} customers`, 'preview-size'],
      ['Counted as of', b.as_of, 'preview-as-of'],
      [holdersName(screen, b.label_code), String(b.label_holders), 'preview-holders'],
      ['Left out of the audience', String(leftOut(p)), 'preview-left-out'],
    ]),
    el('h5', { text: 'Why customers are left out' }),
    testid(
      facts(
        b.reasons.map((r): [string, string, string] => [
          reasonText(r.code, p, (code) => screen.labelName(code)),
          String(r.excluded),
          `preview-reason-${r.code}`,
        ]),
      ),
      'preview-reasons',
    ),
    testid(
      el('p', {
        text: 'Each customer is counted once, under the first reason that applies, in the order shown. The reasons and the audience add up to the customers holding the label.',
      }),
      'preview-note',
    ),
  );
  return wrap;
}

/**
 * What the audience read alone can say, when the breakdown could not be read: the total, the number left
 * out as a difference, and the rules that can leave customers out. The screen says that it is a fallback.
 */
function totalOnly(screen: PlanScreen, p: AudiencePreviewView): HTMLElement {
  const left = leftOut(p);
  const unavailable = 'Unavailable';
  const wrap = el('div');
  const why = p.breakdown_error;
  const notice = testid(
    el('p', {
      cls: 'warning',
      text: `The breakdown by reason could not be read, so this preview shows the total only${
        why === undefined ? '' : `: ${why.message} (${why.code})`
      }.`,
    }),
    'preview-fallback',
  );
  notice.setAttribute('role', 'status');
  wrap.append(
    notice,
    facts([
      ['Estimated audience', `${p.audience.audience_size} customers`, 'preview-size'],
      ['Counted as of', p.audience.as_of, 'preview-as-of'],
      [
        holdersName(screen, p.audience.label_code),
        p.label_holders === undefined ? unavailable : String(p.label_holders),
        'preview-holders',
      ],
      ['Left out of the audience', left === undefined ? unavailable : String(left), 'preview-left-out'],
    ]),
  );
  const rules = el('ul');
  rules.dataset.testid = 'preview-reasons';
  for (const r of reasonsOf(p, (code) => screen.labelName(code)))
    rules.append(testid(el('li', { text: r.text }), `preview-reason-${r.id}`));
  wrap.append(
    el('h5', { text: 'Rules that can leave customers out' }),
    rules,
    testid(
      el('p', {
        text: 'Without the breakdown, the number left out is the customers holding the label minus the audience; it is not split by reason.',
      }),
      'preview-note',
    ),
  );
  return wrap;
}
