import type { KpiDefinitionView, ReportFormat, ReportQuery, ReportView } from '../shared/api';
import { KPI_OPERATIONS } from '../shared/modules';
import {
  reportChart,
  reportDefinitions,
  reportSummary,
  reportTable,
  SYNTHETIC_LABEL,
  type Chart,
  type ReportTable,
} from '../shared/report-table';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, formField, select, testid, textInput } from './dom';
import { latestRequest } from './latest-request';

// Executive reports (F28-14, #883): a KPI over a period, by the dimensions its definition
// supports, with a chart, a summary and each metric's definition. The PDF, CSV and Excel files
// are written by the main process from the report on screen, so their figures match it.

const DAY = 86_400_000;
const GRANULARITIES: [string, string][] = [
  ['month', 'Month'],
  ['week', 'Week'],
  ['day', 'Day'],
];

const isoDay = (t: number): string => new Date(t).toISOString().slice(0, 10);

function dataTable(table: ReportTable): HTMLTableElement {
  const head = el('tr');
  table.columns.forEach((h) => head.append(el('th', { text: h })));
  const body = el('tbody');
  for (const row of table.rows) {
    const tr = el('tr');
    row.forEach((cell, c) => {
      const td = el('td', { text: cell });
      if (table.measures.some((m) => m.column === c)) td.className = 'num';
      if (cell === SYNTHETIC_LABEL) td.className = 'synthetic';
      tr.append(td);
    });
    body.append(tr);
  }
  return testid(el('table', {}, el('thead', {}, head), body), 'report-table');
}

function chartView(chart: Chart): HTMLElement {
  const low = Math.min(0, ...chart.bars.map((b) => b.value));
  const high = Math.max(0, ...chart.bars.map((b) => b.value));
  const list = el('div', { cls: 'chart' });
  for (const bar of chart.bars) {
    const meter = el('meter');
    meter.min = low;
    meter.max = high === low ? low + 1 : high;
    meter.value = bar.value;
    meter.setAttribute('aria-label', `${bar.label}: ${bar.text}`);
    const label = bar.synthetic ? `${bar.label} [${SYNTHETIC_LABEL}]` : bar.label;
    list.append(
      el(
        'div',
        { cls: 'bar' },
        el('span', { cls: 'bar-label', text: label }),
        meter,
        el('span', { text: bar.text }),
      ),
    );
  }
  const note =
    chart.omitted > 0
      ? [el('p', { cls: 'notice', text: `${chart.omitted} more rows are in the table.` })]
      : [];
  return testid(el('section', {}, el('h3', { text: chart.title }), list, ...note), 'report-chart');
}

function reportView(
  report: ReportView,
  canExport: boolean,
  exportTo: (f: ReportFormat) => void,
): HTMLElement {
  const table = reportTable(report);
  const summary = el('dl');
  for (const [name, value] of reportSummary(report, table))
    summary.append(el('dt', { text: name }), el('dd', { text: value }));
  const parts: Node[] = [];
  if (report.synthetic)
    parts.push(
      testid(
        el('p', {
          cls: 'warning',
          text: `${SYNTHETIC_LABEL}: some figures in this report come from seeded data (ADR-0052).`,
        }),
        'report-synthetic',
      ),
    );
  parts.push(el('h3', { text: 'Summary' }), testid(summary, 'report-summary'));
  const chart = reportChart(report, table);
  if (chart !== null) parts.push(chartView(chart));
  parts.push(
    el('h3', { text: 'Figures' }),
    table.rows.length === 0
      ? testid(el('p', { cls: 'notice', text: 'The report has no rows for these filters.' }), 'report-empty')
      : el('div', { cls: 'scroll' }, dataTable(table)),
  );
  const defs = el('dl');
  for (const [term, meaning] of reportDefinitions(report))
    defs.append(el('dt', { text: term }), el('dd', { text: meaning }));
  parts.push(el('h3', { text: 'Definitions' }), testid(defs, 'report-definitions'));
  if (canExport) {
    const buttons = el('div', { cls: 'export' });
    for (const [format, label] of [
      ['pdf', 'Export PDF'],
      ['csv', 'Export CSV'],
      ['xlsx', 'Export Excel'],
    ] as const)
      buttons.append(button(label, `export-${format}`, () => exportTo(format)));
    parts.push(buttons);
  }
  return el('section', {}, ...parts);
}

export function renderReports(host: HTMLElement, ctx: ScreenContext & { canExport?: boolean }): void {
  const { api } = ctx;
  const message = el('div');
  message.setAttribute('role', 'status');
  const output = el('div');
  const controls = el('form');
  controls.noValidate = true;
  const latest = latestRequest();
  host.replaceChildren(el('h2', { text: 'Reports' }), controls, message, output);

  void api.kpiDefinitions().then((res) => {
    if (!res.ok) return void output.replaceChildren(errorBox(res.error));
    const kpis = res.value.filter((d) => ctx.can(KPI_OPERATIONS[d.kpi_code] ?? ''));
    if (kpis.length === 0)
      return void output.replaceChildren(el('p', { cls: 'notice', text: 'No report is open to your role.' }));
    buildControls(kpis);
  });

  function buildControls(kpis: KpiDefinitionView[]): void {
    const kpi = select(
      kpis.map((d) => [d.kpi_code, d.name]),
      kpis[0]?.kpi_code ?? '',
    );
    const today = Date.now();
    const from = textInput(isoDay(today - 30 * DAY), 'date');
    const to = textInput(isoDay(today), 'date');
    const granularity = select(GRANULARITIES, 'month');
    const store = select([['', 'Every store']], '');
    const channel = select([['', 'Every channel']], '');
    const segment = textInput('');
    const dims = el('fieldset', {}, el('legend', { text: 'Group by' }));
    const boxes = new Map<string, HTMLInputElement>();
    void api.stores().then((r) => {
      if (r.ok)
        for (const s of r.value)
          store.append(
            Object.assign(el('option', { text: `${s.name} (#${s.store_id})` }), {
              value: String(s.store_id),
            }),
          );
    });
    void api.listChannels().then((r) => {
      if (r.ok)
        for (const c of r.value)
          channel.append(Object.assign(el('option', { text: c.name }), { value: String(c.id) }));
    });

    const current = (): KpiDefinitionView =>
      kpis.find((d) => d.kpi_code === kpi.value) ?? (kpis[0] as KpiDefinitionView);
    const rebuildDimensions = (): void => {
      const supported = current().dimensions;
      boxes.clear();
      dims.replaceChildren(el('legend', { text: 'Group by' }));
      for (const d of supported) {
        const box = el('input');
        box.type = 'checkbox';
        box.value = d;
        box.dataset.testid = `group-${d}`;
        boxes.set(d, box);
        dims.append(el('label', {}, box, document.createTextNode(` ${d}`)));
      }
      const has = (d: string): boolean => supported.includes(d);
      store.parentElement!.hidden = !has('store');
      channel.parentElement!.hidden = !has('channel');
      segment.parentElement!.hidden = !has('segment');
      granularity.parentElement!.hidden = !has('period');
    };
    kpi.addEventListener('change', rebuildDimensions);

    const run = button('Open report', 'report-open', () => {
      const query: ReportQuery = { kpi: kpi.value, from: from.value, to: to.value };
      const group = [...boxes].filter(([, b]) => b.checked).map(([d]) => d);
      if (group.length > 0) query.group_by = group;
      if (group.includes('period')) query.granularity = granularity.value;
      if (!store.parentElement!.hidden && store.value !== '') query.store_id = Number(store.value);
      if (!channel.parentElement!.hidden && channel.value !== '') query.channel_id = Number(channel.value);
      if (!segment.parentElement!.hidden && segment.value.trim() !== '')
        query.segment_code = segment.value.trim();
      message.replaceChildren();
      output.replaceChildren(testid(el('p', { text: 'Loading…' }), 'report-loading'));
      void latest(
        () => api.kpiReport(query),
        (r) => {
          if (!r.ok) return void output.replaceChildren(errorBox(r.error));
          output.replaceChildren(
            reportView(r.value, ctx.canExport === true, (format) => {
              void api.exportReport(format).then((e) => {
                if (!e.ok) message.replaceChildren(errorBox(e.error));
                else if (e.value === null) message.replaceChildren();
                else
                  message.replaceChildren(
                    testid(
                      el('p', { cls: 'notice', text: `Exported ${e.value.rows} rows to ${e.value.file}.` }),
                      'notice',
                    ),
                  );
              });
            }),
          );
        },
      );
    });
    controls.addEventListener('submit', (event) => {
      event.preventDefault();
      run.click();
    });
    controls.replaceChildren(
      formField('kpi', 'Report', kpi, [], 'r'),
      formField('from', 'From', from, [], 'r'),
      formField('to', 'To', to, [], 'r'),
      dims,
      formField('granularity', 'Period by', granularity, [], 'r'),
      formField('store_id', 'Store', store, [], 'r'),
      formField('channel_id', 'Channel', channel, [], 'r'),
      formField('segment_code', 'Segment code', segment, [], 'r'),
      run,
    );
    rebuildDimensions();
  }
}
