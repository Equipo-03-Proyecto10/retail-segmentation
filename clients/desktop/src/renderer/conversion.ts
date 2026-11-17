import type { ExperimentResultView, ExperimentView, ResultBasis } from '../shared/api';
import {
  ARM_COLUMNS,
  armRows,
  ATTRIBUTION_COLUMNS,
  ATTRIBUTION_NOTE,
  attributionRows,
  comparisonColumns,
  comparisonRows,
  comparisonsOf,
  controlArm,
  percent,
  pendingText,
  statusText,
  UNATTRIBUTED_NOTE,
  unattributedText,
  warningsOf,
  warningTitles,
  windowText,
} from '../shared/conversion';
import {
  formatCount,
  hasControl,
  NO_EFFECT_STATEMENT,
  sampleRatioText,
  SYNTHETIC_LABEL,
} from '../shared/experiments';
import type { ScreenContext } from './catalog';
import { el, errorBox, formField, select, testid } from './dom';
import { balanceTable, designFacts, facts, table } from './experiments';
import { latestRequest } from './latest-request';

// The conversion analysis module (F28-11, #880): one experiment's result as experimentation-service
// computed it, with its uncertainty. Nothing is written. Each arm's counts and its pending customers,
// then every difference with its interval, then attribution in a section of its own. An effect, and
// the word incremental, appear only for a treatment compared with a control; a design with no control
// shows the difference between treatments and says that no effect can be claimed. The warnings of
// the design are drawn above the figures, in a bar that stays in view while the result scrolls, and
// every arm row carries them too, so a table taken on its own does not lose them. Text is set with
// textContent only.

const BASES: [ResultBasis, string][] = [
  ['PRIMARY', 'Stored result (computed once, at the analysis instant)'],
  ['RE_READ', 'Re-read as of now (replaces nothing)'],
];

const STATUS_TEXT = {
  COMPLETE: 'Complete: the estimate is present',
  PENDING: 'Pending: counts only',
  INVALID: 'Invalid: the estimate is withheld',
} as const;

/**
 * The warnings the result must be read with: a bar right under the title that stays in view while
 * the result scrolls, and each warning in words below the facts.
 */
function warningsView(r: ExperimentResultView): { bar: Node[]; details: Node[] } {
  const warnings = warningsOf(r);
  if (warnings.length === 0)
    return {
      bar: [],
      details: [
        testid(
          el('p', { cls: 'notice', text: 'The service raised no warning on this design.' }),
          'design-warnings-none',
        ),
      ],
    };
  const bar = el(
    'div',
    { cls: 'design-warnings' },
    el('strong', { text: 'Read with these warnings: ' }),
    document.createTextNode(warnings.map((w) => w.title).join(' · ')),
  );
  bar.setAttribute('role', 'status');
  bar.dataset.testid = 'design-warnings';
  const list = el('ul', { cls: 'warning' });
  for (const w of warnings) {
    const item = el('li', {}, el('strong', { text: `${w.title}: ` }), document.createTextNode(w.text));
    item.dataset.testid = `warning-${w.code}`;
    list.append(item);
  }
  return { bar: [bar], details: [testid(list, 'design-warning-details')] };
}

/** A line next to a comparison, so the warnings are read with its figures and not only above them. */
function caveat(r: ExperimentResultView, id: string): Node[] {
  const titles = warningTitles(r);
  return titles === '' ? [] : [testid(el('p', { cls: 'caveat', text: `Read with: ${titles}.` }), id)];
}

function comparisonsView(r: ExperimentResultView): Node[] {
  const parts: Node[] = [];
  const status = statusText(r);
  if (status !== null) parts.push(testid(el('p', { cls: 'notice', text: status }), 'result-status-text'));
  const effects = comparisonsOf(r, 'effect');
  if (effects.length > 0)
    parts.push(
      el('h4', { text: 'Effect against the control' }),
      el('p', {
        cls: 'hint',
        text: 'An effect is a treatment compared with the control, over every assigned customer whether exposed or not, with its interval. Only this section may be called incremental.',
      }),
      ...caveat(r, 'effect-caveat'),
      table(comparisonColumns(r, 'effect'), comparisonRows(r, 'effect'), 'effect-table'),
    );
  const differences = comparisonsOf(r, 'treatment_difference');
  if (differences.length > 0)
    parts.push(
      el('h4', { text: 'Difference between treatments' }),
      el('p', {
        cls: 'hint',
        text: 'How one offer differs from the other, with its interval. It is not an effect of the campaign against no treatment.',
      }),
      ...caveat(r, 'difference-caveat'),
      table(
        comparisonColumns(r, 'treatment_difference'),
        comparisonRows(r, 'treatment_difference'),
        'difference-table',
      ),
    );
  // The refusal is said wherever there is no control, with the service's own wording when it sent one.
  if (controlArm(r) === undefined)
    parts.push(
      testid(
        el('p', { cls: 'warning', text: r.effect_refusal?.message ?? NO_EFFECT_STATEMENT }),
        'effect-refusal',
      ),
    );
  if (comparisonsOf(r, 'refused').length > 0)
    parts.push(
      testid(
        el('p', {
          cls: 'error',
          text: 'The service sent an effect for a design that has no control to compare with. It is not shown.',
        }),
        'effect-refused',
      ),
    );
  return parts;
}

function checksView(r: ExperimentResultView): Node[] {
  if (r.checks === undefined)
    return [
      testid(
        el('p', { text: 'No checks yet: they are stored with the result, at the analysis instant.' }),
        'checks-none',
      ),
    ];
  return [
    testid(
      el('p', {
        cls: r.checks.sample_ratio.status === 'FAILED' ? 'warning' : '',
        text: `Sample ratio check: ${sampleRatioText(r.checks.sample_ratio)}`,
      }),
      'check-sample-ratio',
    ),
    el('h5', { text: 'Balance before assignment (a diagnostic only)' }),
    balanceTable(r.checks, r.arms),
  ];
}

/** Attribution, apart from the effect: its own section, every figure labelled Attribution. */
function attributionView(r: ExperimentResultView): HTMLElement {
  const section = el('section', {}, el('h4', { text: 'Attribution' }), el('p', { text: ATTRIBUTION_NOTE }));
  section.dataset.testid = 'attribution';
  section.dataset.label = 'ATTRIBUTION';
  const a = r.attribution;
  if (a === undefined) {
    section.append(
      testid(
        el('p', {
          cls: 'notice',
          text: 'No attribution is written yet: it is written when the conversion window has closed, and an experiment with no campaign has none.',
        }),
        'attribution-none',
      ),
    );
    return section;
  }
  section.append(
    table(ATTRIBUTION_COLUMNS, attributionRows(r), 'attribution-table'),
    testid(el('p', { text: unattributedText(a) }), 'attribution-unattributed'),
    el('p', { text: UNATTRIBUTED_NOTE }),
    el('p', { text: windowText(a) }),
  );
  return section;
}

function resultView(r: ExperimentResultView, name: string | undefined): HTMLElement {
  const header = name === undefined ? `Experiment #${r.experiment_id}` : `${name} (#${r.experiment_id})`;
  const control = controlArm(r);
  const warnings = warningsView(r);
  const parts: Node[] = [
    el('h3', { text: header }),
    ...warnings.bar,
    facts([
      [
        'Reading',
        r.basis === 'PRIMARY'
          ? 'The stored primary result'
          : 'A re-read: it replaces nothing, the stored result stays as it was',
        'result-basis',
      ],
      ['Status', STATUS_TEXT[r.status], 'result-status'],
      ['As of', r.as_of, 'result-as-of'],
      ['Conversion window elapsed', percent(r.elapsed_share), 'result-elapsed'],
      ['Data', r.synthetic ? `${SYNTHETIC_LABEL} (${r.data_origin})` : 'Observed', 'result-data'],
      [
        'Design',
        control === undefined ? 'Offer comparison: no control' : 'Controlled: one control and the treatments',
        'result-design-kind',
      ],
      ['Campaign', r.campaign_id === undefined ? 'None' : `#${r.campaign_id}`],
    ]),
    ...warnings.details,
    el('h4', { text: "Each arm's results" }),
    el('p', {
      cls: 'hint',
      text: 'Every assigned customer counts in the arm they were assigned to, exposed or not. These are observed counts and revenue, not an effect and not attribution.',
    }),
    el('div', { cls: 'scroll' }, testid(table(ARM_COLUMNS, armRows(r), 'arms-results'), 'arms-wrap')),
    testid(el('p', { cls: 'pending', text: pendingText(r) }), 'pending-statement'),
    ...comparisonsView(r),
    el('h4', { text: 'Checks' }),
    ...checksView(r),
    attributionView(r),
    el('h4', { text: 'The design this result was planned under' }),
    designFacts(r.design),
  ];
  const planned = r.design.required_per_arm;
  if (planned !== undefined)
    parts.push(
      testid(
        el('p', {
          text: `Planned: ${formatCount(planned)} customers per arm. Analysed: ${r.arms
            .map((a) => `${a.name} ${formatCount(a.analysed)}`)
            .join(', ')}.`,
        }),
        'planned-vs-analysed',
      ),
    );
  if (r.assumptions.length > 0) {
    const list = el('ul');
    for (const a of r.assumptions) {
      const item = el('li', { text: a.statement });
      item.dataset.code = a.code;
      list.append(item);
    }
    parts.push(el('h4', { text: 'Assumptions' }), testid(list, 'assumptions'));
  }
  return testid(el('section', { cls: 'result' }, ...parts), 'result');
}

export function renderConversion(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const message = testid(el('div'), 'conversion-message');
  const output = testid(el('div', { cls: 'panel' }), 'conversion-output');
  const latest = latestRequest();
  const latestList = latestRequest();
  const names = new Map<number, string>();
  const picker = select([['', 'Choose an experiment']], '');
  const basis = select(BASES, 'PRIMARY');
  const open = el('button', { text: 'Open result' });
  open.type = 'submit';
  open.dataset.testid = 'result-open';
  open.disabled = true;
  picker.addEventListener('change', () => {
    open.disabled = picker.value === '';
  });
  const form = testid(
    el(
      'form',
      { cls: 'filters' },
      formField('experiment_id', 'Experiment (assigned or analysed)', picker, [], 'result'),
      formField('basis', 'Reading', basis, [], 'result'),
      open,
    ),
    'result-form',
  );
  form.noValidate = true;
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const experimentId = Number(picker.value);
    if (picker.value === '') return;
    message.replaceChildren();
    output.replaceChildren(testid(el('p', { text: 'Loading…' }), 'result-loading'));
    void latest(
      () => api.getExperimentResult(experimentId, basis.value as ResultBasis),
      (res) => {
        if (!res.ok) return void output.replaceChildren(errorBox(res.error));
        output.replaceChildren(resultView(res.value, names.get(res.value.experiment_id)));
      },
    );
  });

  /** An experiment has a result only once it is assigned, so a planned one is not offered. */
  function loadExperiments(): void {
    void latestList(
      () =>
        Promise.all([
          api.listExperiments({ state: 'ASSIGNED', page: 1, size: 100 }),
          api.listExperiments({ state: 'ANALYZED', page: 1, size: 100 }),
        ]),
      (results) => {
        const byId = new Map<number, ExperimentView>();
        for (const r of results) {
          if (!r.ok) return void message.replaceChildren(errorBox(r.error));
          for (const e of r.value.items) byId.set(e.experiment_id, e);
        }
        const found = [...byId.values()].sort((a, b) => b.experiment_id - a.experiment_id);
        names.clear();
        for (const e of found) names.set(e.experiment_id, e.name);
        picker.replaceChildren(
          Object.assign(el('option', { text: 'Choose an experiment' }), { value: '' }),
          ...found.map((e) =>
            Object.assign(
              el('option', {
                text: `#${e.experiment_id} ${e.name} (${e.state}${hasControl(e) ? '' : ', no control'})`,
              }),
              { value: String(e.experiment_id) },
            ),
          ),
        );
        if (found.length === 0)
          message.replaceChildren(
            testid(
              el('p', {
                cls: 'notice',
                text: 'No experiment has been assigned yet: a result exists only after assignment.',
              }),
              'no-experiments',
            ),
          );
      },
    );
  }

  host.replaceChildren(
    el('h2', { text: 'Conversion analysis' }),
    el('p', {
      cls: 'hint',
      text: 'Results with their uncertainty: read the difference and its interval, not the totals.',
    }),
    form,
    message,
    output,
  );
  loadExperiments();
}
