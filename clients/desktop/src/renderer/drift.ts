import type {
  DriftAlertQuery,
  DriftAlertView,
  DriftBinView,
  DriftReportQuery,
  DriftReportView,
  DriftStatisticView,
  DriftVariable,
  ModelVersionRefView,
  OutcomeSegmentView,
  OutcomeTestView,
  VariableDriftView,
} from '../shared/api';
import {
  alertSummary,
  binName,
  binRange,
  claimText,
  comparisonLabel,
  comparisonNotice,
  count,
  day,
  gaugeOf,
  KIND_LABEL,
  outcomeMethod,
  outcomeVerdict,
  percent,
  ratesChart,
  referenceText,
  relationText,
  reportSummary,
  resultText,
  reviewLabel,
  REVIEW_NOTE,
  sharesChart,
  stateText,
  statisticName,
  STATE_LABEL,
  thresholdCell,
  THRESHOLDS_NOTE,
  versionText,
  VARIABLE_LABEL,
  windowText,
  LEVEL_LABEL,
} from '../shared/drift';
import { DRIFT_OPERATIONS } from '../shared/modules';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, pager, select, testid, textInput } from './dom';
import { barChart, meter } from './drift-chart';
import { latestRequest } from './latest-request';

// Drift analysis (F28-07, #876): the drift reports and alerts of model-registry. A report opens with
// the reference and the current distribution of each variable charted side by side, the statistic
// and the threshold it is judged by; an alert opens with its explanation: what changed, under which
// model version, and the review it suggests. The service decides every state, level and claim; the
// words are those of F23-07 (ADR-0065): a finding about a distribution is a distribution change,
// and the other phrase is said only of an outcome-test result that names its relation, horizon and
// cohort (`claimText`). Everything the service sent is set with textContent, never as HTML.

const PAGE_SIZE = 20;

const KINDS: [string, string][] = [
  ['', 'Any kind'],
  ['DATA_DRIFT', KIND_LABEL.DATA_DRIFT],
  ['OUTCOME_TEST', KIND_LABEL.OUTCOME_TEST],
];

const COMPARISONS: [string, string][] = [
  ['', 'Any comparison'],
  ['COMPARED', 'Compared'],
  ['INSUFFICIENT_DATA', 'Insufficient data'],
  ['NOT_COMPARED', 'Not compared'],
];

const VARIABLES: [string, string][] = [
  ['', 'Any variable'],
  ...(['SEGMENT_SHARES', 'R', 'F', 'M', 'REPURCHASE_RATE'] as const).map((v): [string, string] => [
    v,
    VARIABLE_LABEL[v],
  ]),
];

const DATA_NAMES = { reference: 'Reference', current: 'Current window' };
const OUTCOME_NAMES = { reference: 'Reference cohort', current: 'Current cohort' };

function cell(content: string | Node, cls?: string): HTMLTableCellElement {
  const td = el('td', cls === undefined ? {} : { cls });
  td.append(content);
  return td;
}

function head(columns: string[]): HTMLElement {
  const tr = el('tr');
  for (const c of columns) {
    const th = el('th', { text: c });
    th.scope = 'col';
    if (c === '') th.setAttribute('aria-label', 'Actions');
    tr.append(th);
  }
  return el('thead', {}, tr);
}

function table(id: string, columns: string[], rows: HTMLElement[]): HTMLElement {
  return el('div', { cls: 'scroll' }, testid(el('table', {}, head(columns), el('tbody', {}, ...rows)), id));
}

function facts(rows: [string, string | Node, string][]): HTMLElement {
  const dl = el('dl', { cls: 'facts' });
  for (const [name, value, id] of rows) {
    const dd = el('dd');
    dd.append(value);
    dl.append(el('dt', { text: name }), testid(dd, id));
  }
  return dl;
}

function field(name: string, control: HTMLElement): HTMLElement {
  const label = el('label', { text: name });
  return el('div', { cls: 'form-field' }, label, control);
}

function statisticsTable(prefix: string, stats: readonly DriftStatisticView[]): HTMLElement {
  const rows = stats.map((s) => {
    const tr = el(
      'tr',
      {},
      cell(statisticName(s)),
      cell(s.value, 'num'),
      cell(s.p_value ?? '—', 'num'),
      cell(thresholdCell(s, 'watch')),
      cell(thresholdCell(s, 'alarm')),
      cell(LEVEL_LABEL[s.level]),
    );
    tr.dataset.level = s.level;
    return tr;
  });
  return table(`statistics-${prefix}`, ['Statistic', 'Value', 'p-value', 'Watch', 'Alarm', 'Level'], rows);
}

/** Each PSI against its two thresholds; a p-value has no such scale and is stated in the table. */
function meters(prefix: string, stats: readonly DriftStatisticView[]): HTMLElement[] {
  const out: HTMLElement[] = [];
  for (const s of stats) {
    const model = gaugeOf(s);
    if (model !== null)
      out.push(
        el(
          'figure',
          { cls: 'viz' },
          el('figcaption', { text: `${statisticName(s)}: ${s.value}, ${LEVEL_LABEL[s.level]}` }),
          meter(model, statisticName(s), `meter-${prefix}-${s.statistic}`),
        ),
      );
  }
  return out;
}

function binsTable(prefix: string, bins: readonly DriftBinView[]): HTMLElement {
  const rows = bins.map((b) => {
    const tr = el(
      'tr',
      {},
      cell(binName(b)),
      cell(binRange(b)),
      cell(percent(b.reference_share), 'num'),
      cell(percent(b.current_share), 'num'),
      cell(b.contribution, 'num'),
    );
    tr.dataset.bin = b.label;
    return tr;
  });
  return table(
    `bins-${prefix}`,
    ['Bin', 'Range', 'Reference share', 'Current share', 'PSI contribution'],
    rows,
  );
}

/** The evidence for one variable: statistics against thresholds, the two distributions, every bin. */
function distribution(
  prefix: string,
  variable: DriftVariable,
  stats: readonly DriftStatisticView[],
  bins: readonly DriftBinView[],
  onlyMoved: boolean,
): HTMLElement[] {
  return [
    statisticsTable(prefix, stats),
    ...meters(prefix, stats),
    barChart(sharesChart(variable, bins, onlyMoved), DATA_NAMES, `chart-${prefix}`),
    binsTable(prefix, bins),
  ];
}

function variableSection(v: VariableDriftView): HTMLElement {
  const state = testid(el('p', { text: stateText(v.state) }), `variable-state-${v.variable}`);
  state.dataset.state = v.state;
  const section = el(
    'section',
    { cls: 'panel' },
    el('h4', { text: `${VARIABLE_LABEL[v.variable]}: ${STATE_LABEL[v.state]}` }),
    state,
    ...distribution(v.variable, v.variable, v.statistics, v.bins, false),
  );
  section.dataset.variable = v.variable;
  return testid(section, `variable-${v.variable}`);
}

function ratesText(r: OutcomeSegmentView['reference']): string {
  return `${count(r.customers)} customers, ${count(r.repurchasers)} repurchased (${percent(r.rate)})`;
}

function outcomeSection(
  o: OutcomeTestView,
  version: ModelVersionRefView,
  persistedOnly: boolean,
): HTMLElement {
  const rows = o.segments.map((s) => {
    const tr = el(
      'tr',
      {},
      cell(s.label_code),
      cell(ratesText(s.reference)),
      cell(ratesText(s.current)),
      cell(s.difference_points ?? '—', 'num'),
      cell(s.z ?? '—', 'num'),
      cell(s.p_value ?? '—', 'num'),
      cell(s.adjusted_p_value ?? '—', 'num'),
      cell(resultText(s)),
      cell(s.persisted ? 'Yes' : 'No'),
    );
    tr.dataset.segment = s.label_code;
    tr.dataset.result = s.result;
    return tr;
  });
  const parts: Node[] = [
    el('h4', { text: 'Outcome test' }),
    facts([
      ['Relation', relationText(o), 'outcome-relation'],
      ['Horizon', `${o.horizon_days} days`, 'outcome-horizon'],
      ['Cohort as of', o.cohort_as_of, 'outcome-cohort'],
      [
        'Thresholds',
        `Holm-adjusted alpha ${o.thresholds.alpha}; a difference of at least ${o.thresholds.min_difference_points} percentage points; at least ${o.thresholds.min_customers_per_side} customers on each side`,
        'outcome-thresholds',
      ],
    ]),
    el('p', { text: outcomeMethod(o) }),
  ];
  // The claim is the service's: present only when a segment persisted, and then it names its evidence.
  const claim =
    o.claim === undefined
      ? testid(el('p', { cls: 'notice', text: outcomeVerdict(o) }), 'outcome-verdict')
      : testid(
          el('p', {
            cls: 'warning',
            text: claimText(
              {
                claim: 'CONCEPT_DRIFT',
                variable: 'REPURCHASE_RATE',
                model_version: version,
                outcome_test: o,
              },
              'full',
            ),
          }),
          'outcome-claim',
        );
  parts.push(
    claim,
    table(
      'segments-table',
      [
        persistedOnly ? 'Segment that persisted' : 'Segment',
        'Reference',
        'Current',
        'Difference (points)',
        'z',
        'p-value',
        'Adjusted p-value',
        'Result',
        'Persisted',
      ],
      rows,
    ),
    barChart(ratesChart(o), OUTCOME_NAMES, 'chart-rates'),
  );
  return testid(el('section', { cls: 'panel' }, ...parts), 'outcome-test');
}

export function renderDrift(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const canReports = ctx.can(DRIFT_OPERATIONS.reports);
  const canAlerts = ctx.can(DRIFT_OPERATIONS.alerts);
  const message = el('div');
  message.setAttribute('role', 'status');

  const reportList = testid(el('div'), 'drift-reports-list');
  const reportDetail = testid(el('section', { cls: 'panel' }), 'drift-report');
  const alertList = testid(el('div'), 'drift-alerts-list');
  const alertDetail = testid(el('section', { cls: 'panel' }), 'drift-alert');
  const latestReports = latestRequest();
  const latestReport = latestRequest();
  const latestAlerts = latestRequest();
  const latestAlert = latestRequest();
  const reportQuery: DriftReportQuery = { page: 1, size: PAGE_SIZE };
  const alertQuery: DriftAlertQuery = { page: 1, size: PAGE_SIZE };

  // ---------- filters ----------

  const reportKind = select(KINDS, '');
  const reportVersion = textInput('', 'number');
  reportVersion.min = '1';
  const reportComparison = select(COMPARISONS, '');
  const reportSearch = el('button', { text: 'Search reports' });
  reportSearch.type = 'submit';
  const reportForm = testid(
    el(
      'form',
      { cls: 'filters' },
      field('Kind', reportKind),
      field('Model version', reportVersion),
      field('Comparison', reportComparison),
      reportSearch,
    ),
    'drift-report-filters',
  );
  reportForm.noValidate = true;
  reportForm.addEventListener('submit', (event) => {
    event.preventDefault();
    delete reportQuery.kind;
    delete reportQuery.model_version_id;
    delete reportQuery.comparison;
    if (reportKind.value !== '') reportQuery.kind = reportKind.value as DriftReportQuery['kind'];
    if (reportVersion.value !== '') reportQuery.model_version_id = Number(reportVersion.value);
    if (reportComparison.value !== '')
      reportQuery.comparison = reportComparison.value as DriftReportQuery['comparison'];
    reportQuery.page = 1;
    void loadReports();
  });

  const alertKind = select(KINDS, '');
  const alertVersion = textInput('', 'number');
  alertVersion.min = '1';
  const alertVariable = select(VARIABLES, '');
  const alertSearch = el('button', { text: 'Search alerts' });
  alertSearch.type = 'submit';
  const alertForm = testid(
    el(
      'form',
      { cls: 'filters' },
      field('Kind', alertKind),
      field('Model version', alertVersion),
      field('Variable', alertVariable),
      alertSearch,
    ),
    'drift-alert-filters',
  );
  alertForm.noValidate = true;
  alertForm.addEventListener('submit', (event) => {
    event.preventDefault();
    delete alertQuery.kind;
    delete alertQuery.model_version_id;
    delete alertQuery.variable;
    if (alertKind.value !== '') alertQuery.kind = alertKind.value as DriftAlertQuery['kind'];
    if (alertVersion.value !== '') alertQuery.model_version_id = Number(alertVersion.value);
    if (alertVariable.value !== '') alertQuery.variable = alertVariable.value as DriftAlertQuery['variable'];
    alertQuery.page = 1;
    void loadAlerts();
  });

  // ---------- the two tabs ----------

  const reportsPanel = testid(
    el('div', {}, el('h3', { text: 'Drift reports' }), reportForm, reportList, reportDetail),
    'drift-reports-panel',
  );
  const alertsPanel = testid(
    el('div', {}, el('h3', { text: 'Drift alerts' }), alertForm, alertList, alertDetail),
    'drift-alerts-panel',
  );
  const tabs = el('div', { cls: 'tabs' });
  tabs.setAttribute('role', 'group');
  tabs.setAttribute('aria-label', 'Drift views');
  let alertsLoaded = false;
  const showTab = (tab: 'reports' | 'alerts'): void => {
    reportsPanel.hidden = tab !== 'reports';
    alertsPanel.hidden = tab !== 'alerts';
    tabs
      .querySelectorAll('button')
      .forEach((b) => b.setAttribute('aria-current', String(b.dataset.tab === tab)));
    if (tab === 'alerts' && !alertsLoaded) {
      alertsLoaded = true;
      void loadAlerts();
    }
  };
  for (const [tab, name, allowed] of [
    ['reports', 'Reports', canReports],
    ['alerts', 'Alerts', canAlerts],
  ] as const) {
    if (!allowed) continue;
    const b = el('button', { text: name });
    b.type = 'button';
    b.dataset.tab = tab;
    b.dataset.testid = `drift-tab-${tab}`;
    b.addEventListener('click', () => showTab(tab));
    tabs.append(b);
  }

  host.replaceChildren(
    el('h2', { text: 'Drift analysis' }),
    el('p', {
      text: 'Compares the customers of each model version with the reference distribution that version was fitted on. A distribution change says how customers are spread now, not why. Only an outcome test on matured cohorts says anything about the relation between a segment and repurchase.',
    }),
    tabs,
    message,
    reportsPanel,
    alertsPanel,
  );

  // ---------- reports ----------

  async function loadReports(): Promise<void> {
    latestReport.begin();
    reportDetail.replaceChildren();
    reportList.replaceChildren(testid(el('p', { text: 'Loading…' }), 'reports-loading'));
    await latestReports(
      () => api.listDriftReports(reportQuery),
      (r) => {
        if (!r.ok) return void reportList.replaceChildren(errorBox(r.error));
        if (r.value.items.length === 0) {
          return void reportList.replaceChildren(
            testid(
              el('p', { cls: 'notice', text: 'No drift report matches these filters.' }),
              'reports-empty',
            ),
          );
        }
        const rows = r.value.items.map((report) => {
          const open = ctx.can(DRIFT_OPERATIONS.report)
            ? button('Open', `open-report-${report.report_id}`, () => void openReport(report.report_id))
            : el('span');
          open.setAttribute('aria-label', `Open report ${report.report_id}`);
          const tr = el(
            'tr',
            {},
            cell(`#${report.report_id}`),
            cell(KIND_LABEL[report.kind]),
            cell(`${report.model_version.name} (${report.model_version.version_id})`),
            cell(day(report.window.end)),
            cell(count(report.window.customer_count), 'num'),
            cell(comparisonLabel(report)),
            cell(reportSummary(report)),
            cell(String(report.alert_ids.length), 'num'),
            cell(open),
          );
          tr.dataset.reportId = String(report.report_id);
          return tr;
        });
        reportList.replaceChildren(
          table(
            'drift-reports-table',
            [
              'Report',
              'Kind',
              'Model version',
              'Window ends',
              'Customers',
              'Comparison',
              'Findings',
              'Alerts',
              '',
            ],
            rows,
          ),
          pager(r.value.page, 'reports', (page) => {
            reportQuery.page = page;
            void loadReports();
          }),
        );
      },
    );
  }

  async function openReport(reportId: number): Promise<void> {
    showTab('reports');
    reportDetail.replaceChildren(testid(el('p', { text: 'Loading…' }), 'report-loading'));
    await latestReport(
      () => api.getDriftReport(reportId),
      (r) => {
        if (!r.ok) return void reportDetail.replaceChildren(errorBox(r.error));
        reportDetail.replaceChildren(reportView(r.value));
        reportDetail.scrollIntoView({ block: 'start' });
      },
    );
  }

  function link(label: string, id: string, open: () => void): HTMLElement {
    const b = button(label, id, open);
    b.classList.add('link');
    return b;
  }

  function reportView(report: DriftReportView): HTMLElement {
    const notice = comparisonNotice(report);
    const previous =
      report.previous_report_id === undefined
        ? el('span', { text: 'None: this is the first report of its version and kind.' })
        : ctx.can(DRIFT_OPERATIONS.report)
          ? link(`Report #${report.previous_report_id}`, 'previous-report', () => {
              void openReport(report.previous_report_id as number);
            })
          : el('span', { text: `Report #${report.previous_report_id}` });
    const raised = el('span');
    if (report.alert_ids.length === 0) raised.append('None');
    for (const alertId of report.alert_ids) {
      raised.append(
        ctx.can(DRIFT_OPERATIONS.alert)
          ? link(`Alert #${alertId}`, `report-alert-${alertId}`, () => void openAlert(alertId))
          : document.createTextNode(`Alert #${alertId} `),
      );
    }
    const parts: Node[] = [
      testid(el('h3', { text: `Report #${report.report_id}: ${KIND_LABEL[report.kind]}` }), 'report-title'),
      facts([
        ['Model version', versionText(report.model_version), 'report-version'],
        ['Window', windowText(report.kind, report.window), 'report-window'],
        ['Reference', referenceText(report.reference), 'report-reference'],
        ['Comparison', comparisonLabel(report), 'report-comparison'],
        ['Computed at', report.computed_at, 'report-computed'],
        ['Previous report', previous, 'report-previous'],
        ['Alerts raised', raised, 'report-alerts'],
      ]),
    ];
    if (notice !== null)
      parts.push(
        testid(el('p', { cls: 'warning', text: notice }), `report-${report.comparison.toLowerCase()}`),
      );
    if (report.variables.length > 0) {
      parts.push(
        el('h3', { text: 'Reference and current distributions' }),
        el('p', { text: THRESHOLDS_NOTE }),
        ...report.variables.map(variableSection),
      );
    }
    if (report.outcome_test !== undefined)
      parts.push(outcomeSection(report.outcome_test, report.model_version, false));
    return el('div', {}, ...parts);
  }

  // ---------- alerts ----------

  async function loadAlerts(): Promise<void> {
    latestAlert.begin();
    alertDetail.replaceChildren();
    alertList.replaceChildren(testid(el('p', { text: 'Loading…' }), 'alerts-loading'));
    await latestAlerts(
      () => api.listDriftAlerts(alertQuery),
      (r) => {
        if (!r.ok) return void alertList.replaceChildren(errorBox(r.error));
        if (r.value.items.length === 0) {
          return void alertList.replaceChildren(
            testid(el('p', { cls: 'notice', text: 'No drift alert matches these filters.' }), 'alerts-empty'),
          );
        }
        const rows = r.value.items.map((alert) => {
          const open = ctx.can(DRIFT_OPERATIONS.alert)
            ? button('Open', `open-alert-${alert.alert_id}`, () => void openAlert(alert.alert_id))
            : el('span');
          open.setAttribute('aria-label', `Open alert ${alert.alert_id}`);
          const tr = el(
            'tr',
            {},
            cell(`#${alert.alert_id}`),
            cell(day(alert.raised_at)),
            cell(alertSummary(alert)),
            cell(VARIABLE_LABEL[alert.variable]),
            cell(`${alert.model_version.name} (${alert.model_version.version_id})`),
            cell(`#${alert.report_id}`),
            cell(open),
          );
          tr.dataset.alertId = String(alert.alert_id);
          return tr;
        });
        alertList.replaceChildren(
          table(
            'drift-alerts-table',
            ['Alert', 'Raised', 'Finding', 'Variable', 'Model version', 'Report', ''],
            rows,
          ),
          pager(r.value.page, 'alerts', (page) => {
            alertQuery.page = page;
            void loadAlerts();
          }),
        );
      },
    );
  }

  async function openAlert(alertId: number): Promise<void> {
    showTab('alerts');
    alertDetail.replaceChildren(testid(el('p', { text: 'Loading…' }), 'alert-loading'));
    await latestAlert(
      () => api.getDriftAlert(alertId),
      (r) => {
        if (!r.ok) return void alertDetail.replaceChildren(errorBox(r.error));
        alertDetail.replaceChildren(alertView(r.value));
        alertDetail.scrollIntoView({ block: 'start' });
      },
    );
  }

  /** The explanation first: what changed, under which version, and the review it suggests. */
  function alertView(alert: DriftAlertView): HTMLElement {
    const review = `${reviewLabel(alert.suggested_review.code)}. ${alert.suggested_review.text}`;
    const explanation = testid(
      el(
        'section',
        { cls: 'panel' },
        el('h4', { text: 'Explanation' }),
        facts([
          ['What changed', claimText(alert, 'full'), 'alert-change'],
          ['Variable', VARIABLE_LABEL[alert.variable], 'alert-variable'],
          ['Model version', versionText(alert.model_version), 'alert-version'],
          ['Suggested review', review, 'alert-review'],
        ]),
        el('p', { cls: 'notice', text: REVIEW_NOTE }),
      ),
      'alert-explanation',
    );
    const reportLink = ctx.can(DRIFT_OPERATIONS.report)
      ? link(`Report #${alert.report_id}`, 'alert-report', () => void openReport(alert.report_id))
      : el('span', { text: `Report #${alert.report_id}` });
    const previousLink = ctx.can(DRIFT_OPERATIONS.report)
      ? link(`Report #${alert.previous_report_id}`, 'alert-previous', () => {
          void openReport(alert.previous_report_id);
        })
      : el('span', { text: `Report #${alert.previous_report_id}` });
    const parts: Node[] = [
      testid(
        el('h3', { text: `Alert #${alert.alert_id}: ${VARIABLE_LABEL[alert.variable]}` }),
        'alert-title',
      ),
      explanation,
      facts([
        ['Kind', KIND_LABEL[alert.kind], 'alert-kind'],
        ['Window', windowText(alert.kind, alert.window), 'alert-window'],
        ['Reference', referenceText(alert.reference), 'alert-reference'],
        ['Raised at', alert.raised_at, 'alert-raised'],
        ['Raised by report', reportLink, 'alert-report-link'],
        ['Same finding in the report before', previousLink, 'alert-previous-link'],
      ]),
    ];
    if (alert.outcome_test !== undefined) {
      parts.push(outcomeSection(alert.outcome_test, alert.model_version, true));
    } else {
      parts.push(
        el('h4', { text: 'How much' }),
        el('p', { text: THRESHOLDS_NOTE }),
        ...distribution('alert', alert.variable, alert.statistics, alert.bins, true),
      );
    }
    return el('div', {}, ...parts);
  }

  showTab('reports');
  if (canReports) void loadReports();
  else if (canAlerts) showTab('alerts');
}
