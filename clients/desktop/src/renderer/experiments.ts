import type {
  ArmKind,
  AssignmentChecksView,
  AssignmentView,
  CampaignView,
  ExperimentDataOrigin,
  ExperimentDraft,
  ExperimentMetric,
  ExperimentState,
  ExperimentView,
} from '../shared/api';
import {
  armName,
  balanceText,
  BALANCE_MEASURE_TEXT,
  ESTIMATOR_TEXT,
  FLAG_TEXT,
  formatCount,
  hasControl,
  isSynthetic,
  MAX_ARMS,
  MDE_UNIT_TEXT,
  METRIC_TEXT,
  metricFigure,
  MIN_ARMS,
  NO_EFFECT_STATEMENT,
  readDesign,
  sampleRatioText,
  sampleSizeOf,
  sampleSizeText,
  SYNTHETIC_LABEL,
} from '../shared/experiments';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, formField, markDetails, pager, select, show, testid, textInput } from './dom';
import { latestRequest } from './latest-request';

// The A/B experiment module (F28-10, #879): the experiments experimentation-service holds, the plan
// form, and the assignment of a planned frame. Every figure is the service's. The screen shows the
// customers each arm needs as soon as a plan exists and before anyone is assigned, flags an
// underpowered plan, shows the sample ratio and balance checks of an assignment, and says in plain
// words that no effect can be claimed for a design with no control. Text is set with textContent only.

const PLAN_FIELDS = [
  'name',
  'campaign_id',
  'primary_metric',
  'starts_on',
  'conversion_window_days',
  'minimum_detectable_effect',
  'alpha',
  'power',
  'arms',
];

const STATES: ExperimentState[] = ['PLANNED', 'ASSIGNED', 'ANALYZED'];
const ORIGINS: ExperimentDataOrigin[] = ['OBSERVED', 'SEEDED', 'INJECTED'];
const METRICS: ExperimentMetric[] = ['CONVERSION', 'REVENUE_PER_CUSTOMER'];
const KINDS: [ArmKind, string][] = [
  ['CONTROL', 'Control (held back)'],
  ['TREATMENT', 'Treatment'],
];

const SIZES: [string, string][] = [
  ['20', '20 a page'],
  ['50', '50 a page'],
  ['100', '100 a page'],
];

const today = (): string => new Date().toISOString().slice(0, 10);

function facts(rows: [string, string, string?][]): HTMLElement {
  const list = el('dl', { cls: 'facts' });
  for (const [name, value, id] of rows) {
    const dd = el('dd', { text: value });
    if (id !== undefined) dd.dataset.testid = id;
    list.append(el('dt', { text: name }), dd);
  }
  return list;
}

function table(headers: string[], rows: string[][], id: string): HTMLElement {
  const head = el('tr');
  for (const h of headers) head.append(el('th', { text: h }));
  const body = el('tbody');
  for (const r of rows) body.append(el('tr', {}, ...r.map((cell) => el('td', { text: cell }))));
  return el('div', { cls: 'scroll' }, testid(el('table', {}, el('thead', {}, head), body), id));
}

export function designKindText(e: Pick<ExperimentView, 'design_kind' | 'arms'>): string {
  return hasControl(e)
    ? 'Controlled: one control and the treatments'
    : 'Offer comparison: two treatments and no control';
}

function sampleSizeBlock(e: ExperimentView): HTMLElement {
  const r = sampleSizeOf(e);
  if (r === null)
    return testid(
      el('p', {
        cls: 'notice',
        text: 'No sample size was planned: this experiment was assigned under rule V0, which recorded no design values.',
      }),
      'sample-size',
    );
  const metric = e.design.primary_metric;
  const parts: Node[] = [
    testid(
      el('p', { cls: 'sample-size-required', text: `Customers needed per arm: ${formatCount(r.required)}` }),
      'sample-size-required',
    ),
    testid(el('p', { text: sampleSizeText(r) }), 'sample-size-supply'),
  ];
  if (r.underpowered) {
    const detail = [
      'Underpowered. The frame cannot supply the customers each arm needs.',
      r.smallestDetectable === undefined
        ? ''
        : ` The smallest effect it can detect is ${metricFigure(metric, r.smallestDetectable)}${
            r.minimumDetectable === undefined
              ? '.'
              : `, above the planned ${metricFigure(metric, r.minimumDetectable)}.`
          }`,
      ' A result that is not significant will be inconclusive, never "no effect".',
      e.state === 'PLANNED' ? ' Assigning needs an explicit acknowledgement.' : '',
    ].join('');
    parts.push(testid(el('p', { cls: 'warning', text: detail }), 'underpowered'));
  } else {
    parts.push(
      testid(
        el('p', {
          cls: 'notice',
          text: 'Not underpowered: the service did not flag this plan, so the frame supplies the customers each arm needs.',
        }),
        'powered',
      ),
    );
  }
  return testid(el('div', {}, ...parts), 'sample-size');
}

function designFacts(e: ExperimentView): HTMLElement {
  const d = e.design;
  const rows: [string, string, string?][] = [
    ['Primary metric', METRIC_TEXT[d.primary_metric], 'design-primary-metric'],
  ];
  if (d.secondary_metric !== undefined)
    rows.push(['Secondary metric (reported with its interval, no claim)', METRIC_TEXT[d.secondary_metric]]);
  if (d.estimator !== undefined) rows.push(['Estimator', ESTIMATOR_TEXT[d.estimator]]);
  if (d.alpha !== undefined) rows.push(['Alpha (two-sided, family-wise)', d.alpha]);
  if (d.comparison_alpha !== undefined) rows.push(['Alpha of each comparison', d.comparison_alpha]);
  if (d.power !== undefined) rows.push(['Power', d.power]);
  if (d.minimum_detectable_effect !== undefined)
    rows.push(['Minimum detectable effect', metricFigure(d.primary_metric, d.minimum_detectable_effect)]);
  if (d.baseline !== undefined)
    rows.push(['Baseline (the frame before it was taken)', d.baseline, 'design-baseline']);
  if (d.baseline_std_dev !== undefined) rows.push(['Baseline standard deviation', d.baseline_std_dev]);
  if (d.smallest_detectable_effect !== undefined)
    rows.push([
      'Smallest effect the frame can detect',
      metricFigure(d.primary_metric, d.smallest_detectable_effect),
      'design-smallest-effect',
    ]);
  if (d.allocation !== undefined) rows.push(['Allocation', 'Equal: arms differ by at most one customer']);
  rows.push(['Conversion window', `${d.conversion_window_days} days, fixed at planning`]);
  if (d.settle_days !== undefined) rows.push(['Settle period', `${d.settle_days} days after the window`]);
  rows.push(['Starts on', d.starts_on]);
  rows.push([
    'Analysis instant',
    d.analysis_at ?? 'Fixed when the frame is assigned; the result is read once, then',
    'design-analysis-at',
  ]);
  return testid(facts(rows), 'design-facts');
}

function balanceTable(c: AssignmentChecksView, arms: ExperimentView['arms']): HTMLElement {
  if (c.balance.length === 0)
    return testid(el('p', { text: 'No balance measure was stored.' }), 'balance-none');
  return testid(
    table(
      ['Measure', 'Arm', 'Against', 'Standardized difference', 'Threshold', 'Reading'],
      c.balance.map((b) => [
        BALANCE_MEASURE_TEXT[b.measure],
        armName(arms, b.arm_id),
        armName(arms, b.reference_arm_id),
        b.standardized_mean_difference,
        b.threshold,
        balanceText(b),
      ]),
      'balance-table',
    ),
    'balance',
  );
}

/** What an assignment stored: the arms it dealt, and the checks that run with it. */
function assignmentView(a: AssignmentView, e: ExperimentView): HTMLElement {
  const parts: Node[] = [
    facts([
      ['Assigned at', a.assigned_at],
      ['Customers assigned', formatCount(a.assigned_total), 'assigned-total'],
      [
        'Underpowered plan acknowledged',
        a.acknowledged_underpowered ? 'Yes, recorded' : 'No acknowledgement was needed',
      ],
    ]),
    table(
      ['Arm', 'Kind', 'Customers'],
      a.arms.map((x) => [armName(e.arms, x.arm_id), x.kind, formatCount(x.assigned)]),
      'assignment-arms',
    ),
  ];
  const checks = a.checks;
  if (checks === undefined) {
    parts.push(
      testid(
        el('p', {
          cls: 'notice',
          text: 'No checks were stored for this assignment (rule V0): its arms cannot be recomputed and it is not called verified.',
        }),
        'checks-none',
      ),
    );
  } else {
    parts.push(
      el('h5', { text: 'Checks' }),
      testid(
        el('p', {
          text: checks.recomputation_matches
            ? 'Recomputation: passed. Recomputing every assignment from the frame, the salt and the arm order gave the stored arms.'
            : 'Recomputation: failed. Recomputing the assignment did not give the stored arms, so it cannot be called verified.',
        }),
        'check-recomputation',
      ),
      testid(
        el('p', {
          cls: checks.sample_ratio.status === 'FAILED' ? 'warning' : '',
          text: `Sample ratio check: ${sampleRatioText(checks.sample_ratio)}`,
        }),
        'check-sample-ratio',
      ),
      el('h5', { text: 'Balance before assignment' }),
      balanceTable(checks, e.arms),
    );
  }
  return testid(el('div', {}, ...parts), 'assignment-summary');
}

export function renderExperiments(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const message = testid(el('div'), 'experiments-message');
  const tableArea = el('div');
  const detail = testid(el('section', { cls: 'panel' }), 'experiment-detail');
  const dialog = testid(el('dialog'), 'experiment-dialog');
  const planPanel = testid(
    el('details', { cls: 'panel' }, el('summary', { text: 'Plan an experiment' })),
    'plan-panel',
  );
  const renderLatestList = latestRequest();
  const renderLatestDetail = latestRequest();
  const renderLatestCampaign = latestRequest();
  const renderLatestCampaigns = latestRequest();
  let query: {
    campaign_id?: number;
    state?: ExperimentState;
    data_origin?: ExperimentDataOrigin;
    page: number;
    size: number;
  } = { page: 1, size: 20 };

  // ---------- the list ----------

  const state = select([['', 'Any state'], ...STATES.map((s): [string, string] => [s, s])], '');
  const origin = select([['', 'Any origin'], ...ORIGINS.map((s): [string, string] => [s, s])], '');
  const campaignFilter = textInput('', 'number');
  campaignFilter.min = '1';
  const size = select(SIZES, '20');
  const search = el('button', { text: 'Search' });
  search.type = 'submit';
  const filters = testid(
    el(
      'form',
      { cls: 'filters' },
      formField('state', 'State', state, [], 'filter'),
      formField('data_origin', 'Data origin', origin, [], 'filter'),
      formField('campaign_id', 'Campaign id', campaignFilter, [], 'filter'),
      formField('size', 'Page size', size, [], 'filter'),
      search,
    ),
    'experiment-filters',
  );
  filters.noValidate = true;
  filters.addEventListener('submit', (event) => {
    event.preventDefault();
    query = { page: 1, size: Number(size.value) };
    if (state.value) query.state = state.value as ExperimentState;
    if (origin.value) query.data_origin = origin.value as ExperimentDataOrigin;
    if (campaignFilter.value.trim() !== '') query.campaign_id = Number(campaignFilter.value);
    void load();
  });

  async function load(): Promise<void> {
    await renderLatestList(
      () => api.listExperiments(query),
      (res) => {
        if (!res.ok) return void tableArea.replaceChildren(errorBox(res.error));
        const head = el('tr');
        for (const h of [
          'ID',
          'Name',
          'Campaign',
          'State',
          'Design',
          'Primary metric',
          'Customers per arm',
          'Flags',
          'Data',
          '',
        ])
          head.append(el('th', { text: h }));
        const body = el('tbody');
        for (const e of res.value.items) {
          const row = el(
            'tr',
            {},
            el('td', { text: String(e.experiment_id) }),
            el('td', { text: e.name }),
            el('td', { text: e.campaign_id === undefined ? 'None' : `#${e.campaign_id}` }),
            testid(el('td', { text: e.state }), `experiment-state-${e.experiment_id}`),
            el('td', { text: hasControl(e) ? 'Controlled' : 'Offer comparison (no control)' }),
            el('td', { text: e.design.primary_metric }),
            el('td', {
              cls: 'num',
              text:
                e.design.required_per_arm === undefined
                  ? 'Not planned'
                  : formatCount(e.design.required_per_arm),
            }),
            testid(
              el('td', {
                text: e.flags.length === 0 ? 'None' : e.flags.map((f) => FLAG_TEXT[f].title).join(', '),
              }),
              `experiment-flags-${e.experiment_id}`,
            ),
            el('td', { text: isSynthetic(e.data_origin) ? SYNTHETIC_LABEL : 'Observed' }),
            el(
              'td',
              {},
              button('View', `view-experiment-${e.experiment_id}`, () => void open(e.experiment_id)),
            ),
          );
          row.dataset.experimentId = String(e.experiment_id);
          body.append(row);
        }
        tableArea.replaceChildren(
          el(
            'div',
            { cls: 'scroll' },
            testid(el('table', {}, el('thead', {}, head), body), 'experiments-table'),
          ),
          pager(res.value.page, 'experiments', (page) => {
            query = { ...query, page };
            void load();
          }),
        );
      },
    );
  }

  // ---------- one experiment ----------

  async function open(experimentId: number): Promise<void> {
    await renderLatestDetail(
      () => api.getExperiment(experimentId),
      (res) => {
        if (!res.ok) return void detail.replaceChildren(errorBox(res.error));
        showExperiment(res.value);
      },
    );
  }

  function showExperiment(e: ExperimentView): void {
    const campaignText = el('dd', {
      text: e.campaign_id === undefined ? 'None: it has conversions but no attribution' : `#${e.campaign_id}`,
    });
    campaignText.dataset.testid = 'experiment-campaign';
    const campaign = e.campaign_id;
    if (campaign !== undefined && ctx.can('getCampaign')) {
      // The name belongs to campaigns-service; the id stays when it cannot be read.
      void renderLatestCampaign(
        () => api.getCampaign(campaign),
        (res) => {
          if (res.ok) campaignText.textContent = `${res.value.name} (#${campaign})`;
        },
      );
    }
    const summary = el('dl', { cls: 'facts' });
    const row = (name: string, value: Node | string, id?: string): void => {
      const dd = typeof value === 'string' ? el('dd', { text: value }) : el('dd', {}, value);
      if (id !== undefined) dd.dataset.testid = id;
      summary.append(el('dt', { text: name }), dd);
    };
    row('State', e.state, 'experiment-state');
    summary.append(el('dt', { text: 'Campaign' }), campaignText);
    row('Design', designKindText(e), 'experiment-design-kind');
    row(
      'Assignment rule',
      e.assignment_rule === 'V1'
        ? 'V1: a stored frame and a published salt, so anyone can recompute the arms'
        : 'V0: the earlier shuffle. Readable, but with no frame, salt or key it cannot be recomputed and is not called verified',
      'experiment-rule',
    );
    row(
      'Data',
      isSynthetic(e.data_origin) ? `${SYNTHETIC_LABEL} (${e.data_origin})` : 'Observed',
      'experiment-origin',
    );
    if (e.planned_at !== undefined) row('Planned at', e.planned_at);

    const parts: Node[] = [el('h3', { text: `${e.name} (#${e.experiment_id})` })];
    if (!hasControl(e))
      parts.push(testid(el('p', { cls: 'warning', text: NO_EFFECT_STATEMENT }), 'no-effect-notice'));
    if (e.flags.length > 0) {
      const list = el('ul');
      for (const f of e.flags)
        list.append(
          el(
            'li',
            {},
            el('strong', { text: `${FLAG_TEXT[f].title}: ` }),
            document.createTextNode(FLAG_TEXT[f].text),
          ),
        );
      parts.push(
        testid(
          el('div', { cls: 'warning' }, el('strong', { text: 'Design flags' }), list),
          'experiment-flags',
        ),
      );
    }
    parts.push(summary, el('h4', { text: 'Sample size' }), sampleSizeBlock(e));
    parts.push(el('h4', { text: 'Design' }), designFacts(e));
    parts.push(
      el('h4', { text: 'Arms' }),
      table(
        ['Position', 'Kind', 'Name', 'What it receives'],
        e.arms.map((a) => [String(a.position), a.kind, a.name, a.treatment_description]),
        'arms-table',
      ),
    );
    if (e.frame !== undefined) {
      const f = e.frame;
      parts.push(
        el('h4', { text: 'Frame' }),
        facts([
          ['Taken at', f.taken_at],
          ['Customers', formatCount(f.size), 'frame-size'],
          ['Target label', f.label_code],
          ...(f.model_version_id === undefined
            ? []
            : ([['Model version', String(f.model_version_id)]] as [string, string][])),
          ['Digest', f.digest],
          ...(e.salt === undefined
            ? []
            : ([['Salt (published, not a secret)', e.salt]] as [string, string][])),
        ]),
      );
      if (f.exclusions.length > 0)
        parts.push(
          table(
            ['Excluded because', 'Customers'],
            f.exclusions.map((x) => [x.reason, formatCount(x.count)]),
            'exclusions-table',
          ),
        );
    }
    parts.push(el('h4', { text: 'Assignment' }));
    if (e.assignment !== undefined) parts.push(assignmentView(e.assignment, e));
    else {
      parts.push(testid(el('p', { text: 'Nobody is assigned yet.' }), 'not-assigned'));
      if (e.state === 'PLANNED' && ctx.can('assignExperiment')) parts.push(assignForm(e));
    }
    detail.replaceChildren(...parts);
    detail.scrollIntoView({ block: 'start' });
  }

  function assignForm(e: ExperimentView): HTMLElement {
    const underpowered = e.flags.includes('UNDERPOWERED');
    const ack = el('input');
    ack.type = 'checkbox';
    ack.name = 'acknowledge_underpowered';
    ack.id = 'assign-acknowledge';
    ack.dataset.testid = 'acknowledge-underpowered';
    const go = el('button', { text: 'Assign the frame' });
    go.type = 'submit';
    go.dataset.testid = 'assign';
    go.disabled = underpowered;
    ack.addEventListener('change', () => {
      go.disabled = underpowered && !ack.checked;
    });
    const parts: Node[] = [];
    if (underpowered) {
      const label = el('label', {
        text: ' I acknowledge that this design is underpowered: a result that is not significant will be inconclusive, never "no effect".',
      });
      label.htmlFor = ack.id;
      parts.push(el('div', { cls: 'acknowledge' }, ack, label));
    }
    parts.push(go);
    const form = testid(el('form', {}, ...parts), 'assign-form');
    form.noValidate = true;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      confirmAssign(e, underpowered && ack.checked);
    });
    return form;
  }

  function confirmAssign(e: ExperimentView, acknowledged: boolean): void {
    const confirm = button('Confirm assignment', 'confirm-assign', () => {
      dialog.close();
      const isCurrent = renderLatestDetail.begin();
      void api.assignExperiment(e.experiment_id, acknowledged).then(async (res) => {
        if (!isCurrent()) return;
        show(
          message,
          res,
          res.ok
            ? `Experiment #${e.experiment_id} assigned: ${formatCount(res.value.assigned_total)} customers.`
            : '',
        );
        // Whatever happened, read the experiment as the service now holds it.
        await open(e.experiment_id);
        await load();
      });
    });
    const cancel = button('Cancel', 'cancel-assign', () => dialog.close());
    dialog.replaceChildren(
      el('h3', { text: 'Assign the frame' }),
      el('p', {
        text: `Every customer of the frame is dealt to an arm of "${e.name}" in one step.`,
      }),
      testid(
        el('p', {
          cls: 'warning',
          text: 'The assignment is written once and never rewritten. The checks run with it and are shown afterwards.',
        }),
        'assign-warning',
      ),
      confirm,
      cancel,
    );
    dialog.showModal();
  }

  // ---------- the plan ----------

  interface ArmRow {
    kind: ArmKind;
    name: string;
    description: string;
  }
  let arms: ArmRow[] = [
    { kind: 'CONTROL', name: '', description: '' },
    { kind: 'TREATMENT', name: '', description: '' },
  ];
  const armsBox = el('fieldset');
  armsBox.setAttribute('name', 'arms');
  const armsWrap = el('div', {}, armsBox);
  const reading = testid(el('div'), 'design-reading');
  const planName = textInput('');
  const planCampaign = select([['', 'Choose a campaign']], '');
  const planMetric = select(
    METRICS.map((m): [string, string] => [m, METRIC_TEXT[m]]),
    'CONVERSION',
  );
  const mdeHint = el('p', { cls: 'hint', text: MDE_UNIT_TEXT.CONVERSION });
  planMetric.addEventListener('change', () => {
    mdeHint.textContent = MDE_UNIT_TEXT[planMetric.value as ExperimentMetric];
  });
  const planStarts = textInput(today(), 'date');
  const planWindow = textInput('', 'number');
  planWindow.min = '1';
  planWindow.max = '365';
  const planMde = textInput('');
  const planAlpha = textInput('');
  planAlpha.placeholder = '0.05 if left empty';
  const planPower = textInput('');
  planPower.placeholder = '0.80 if left empty';
  const planSubmit = el('button', { text: 'Plan the experiment' });
  planSubmit.type = 'submit';
  planSubmit.dataset.testid = 'plan-submit';

  function drawReading(): void {
    const r = readDesign(arms);
    reading.dataset.design = r.design;
    reading.className = r.noControl ? 'warning' : 'notice';
    const parts: Node[] = [
      testid(el('p', { text: r.statement }), r.noControl ? 'no-effect-notice-form' : 'effect-notice-form'),
    ];
    if (r.problems.length > 0) {
      const list = el('ul');
      for (const p of r.problems) list.append(el('li', { text: p }));
      parts.push(testid(list, 'design-problems'));
    }
    reading.replaceChildren(...parts);
    planSubmit.disabled = r.design === 'invalid';
  }

  function drawArms(): void {
    const rows = arms.map((arm, i) => {
      const n = i + 1;
      const kind = select(KINDS, arm.kind);
      kind.addEventListener('change', () => {
        arm.kind = kind.value as ArmKind;
        drawReading();
      });
      const name = textInput(arm.name);
      name.addEventListener('input', () => {
        arm.name = name.value;
      });
      const description = textInput(arm.description);
      description.addEventListener('input', () => {
        arm.description = description.value;
      });
      const remove = button('Remove', `remove-arm-${n}`, () => {
        arms = arms.filter((_, j) => j !== i);
        drawArms();
      });
      remove.disabled = arms.length <= MIN_ARMS;
      return el(
        'div',
        { cls: 'arm-row' },
        formField(`arm_${n}_kind`, `Arm ${n}`, kind, [], 'plan'),
        formField(`arm_${n}_name`, 'Name', name, [], 'plan'),
        formField(
          `arm_${n}_treatment_description`,
          'What it receives (for a control, what it is held back from)',
          description,
          [],
          'plan',
        ),
        remove,
      );
    });
    const add = button('Add an arm', 'add-arm', () => {
      arms = [...arms, { kind: 'TREATMENT', name: '', description: '' }];
      drawArms();
    });
    add.disabled = arms.length >= MAX_ARMS;
    armsBox.replaceChildren(el('legend', { text: 'Arms' }), ...rows, add);
    drawReading();
  }

  function draft(): ExperimentDraft {
    const out: ExperimentDraft = {
      name: planName.value,
      campaign_id: planCampaign.value === '' ? 0 : Number(planCampaign.value),
      primary_metric: planMetric.value as ExperimentMetric,
      starts_on: planStarts.value,
      conversion_window_days: planWindow.value.trim() === '' ? 0 : Number(planWindow.value),
      minimum_detectable_effect: planMde.value,
      arms: arms.map((a) => ({ kind: a.kind, name: a.name, treatment_description: a.description })),
    };
    if (planAlpha.value.trim() !== '') out.alpha = planAlpha.value;
    if (planPower.value.trim() !== '') out.power = planPower.value;
    return out;
  }

  function planFieldNames(): string[] {
    return [
      ...PLAN_FIELDS,
      ...arms.flatMap((_, i) => [
        `arm_${i + 1}_kind`,
        `arm_${i + 1}_name`,
        `arm_${i + 1}_treatment_description`,
      ]),
    ];
  }

  function confirmPlan(form: HTMLFormElement, d: ExperimentDraft): void {
    const confirm = button('Freeze the plan', 'confirm-plan', () => {
      dialog.close();
      const isCurrent = renderLatestDetail.begin();
      void api.planExperiment(d).then(async (res) => {
        if (!isCurrent()) return;
        show(
          message,
          res,
          res.ok ? `Experiment #${res.value.experiment_id} planned. Nobody is assigned yet.` : '',
          planFieldNames(),
        );
        if (res.ok) {
          // The plan is frozen: close the form so the sample size it produced is what is on screen.
          planPanel.open = false;
          showExperiment(res.value);
          await load();
        } else markDetails(form, res.error.details);
      });
    });
    const cancel = button('Cancel', 'cancel-plan', () => dialog.close());
    dialog.replaceChildren(
      el('h3', { text: 'Plan this experiment' }),
      el('p', {
        text: 'The service takes the frame now, estimates the baseline from it and computes the customers each arm needs. You will see them before anyone is assigned.',
      }),
      testid(
        el('p', {
          cls: 'warning',
          text: 'A plan is frozen: it cannot be edited or deleted. To change a design, plan a new experiment.',
        }),
        'plan-warning',
      ),
      confirm,
      cancel,
    );
    dialog.showModal();
  }

  function planForm(): HTMLElement {
    const form = testid(
      el(
        'form',
        {},
        formField('name', 'Name', planName, [], 'plan'),
        formField('campaign_id', 'Campaign (its target label defines the frame)', planCampaign, [], 'plan'),
        formField(
          'primary_metric',
          'Primary metric (declared before assignment, one only)',
          planMetric,
          [],
          'plan',
        ),
        formField('starts_on', 'Starts on', planStarts, [], 'plan'),
        formField(
          'conversion_window_days',
          'Conversion window, in days (fixed here, never changed)',
          planWindow,
          [],
          'plan',
        ),
        formField('minimum_detectable_effect', 'Minimum detectable effect', planMde, [], 'plan'),
        mdeHint,
        formField('alpha', 'Alpha (optional)', planAlpha, [], 'plan'),
        formField('power', 'Power (optional)', planPower, [], 'plan'),
        armsWrap,
        reading,
        planSubmit,
      ),
      'plan-form',
    );
    form.noValidate = true;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      form.querySelectorAll('.field-detail').forEach((old) => old.remove());
      confirmPlan(form, draft());
    });
    return form;
  }

  /** Campaigns an experiment can attach to: a finished or cancelled one is refused by the service (RN-49). */
  function loadCampaigns(): void {
    void renderLatestCampaigns(
      () =>
        Promise.all([
          api.listCampaigns({ status: 'DRAFT', page: 1, size: 100 }),
          api.listCampaigns({ status: 'ACTIVE', page: 1, size: 100 }),
        ]),
      (results) => {
        const byId = new Map<number, CampaignView>();
        for (const r of results) {
          if (!r.ok) return void message.replaceChildren(errorBox(r.error));
          for (const c of r.value.items) byId.set(c.campaign_id, c);
        }
        const found = [...byId.values()].sort((a, b) => a.campaign_id - b.campaign_id);
        planCampaign.replaceChildren(
          Object.assign(el('option', { text: 'Choose a campaign' }), { value: '' }),
          ...found.map((c) =>
            Object.assign(el('option', { text: `${c.name} (#${c.campaign_id}, ${c.status})` }), {
              value: String(c.campaign_id),
            }),
          ),
        );
      },
    );
  }

  const parts: Node[] = [el('h2', { text: 'A/B experiments' })];
  if (ctx.can('planExperiment')) {
    planPanel.append(planForm());
    parts.push(planPanel);
    drawArms();
    if (ctx.can('listCampaigns')) loadCampaigns();
  }
  parts.push(filters, message, tableArea, detail, dialog);
  host.replaceChildren(...parts);
  void load();
}
