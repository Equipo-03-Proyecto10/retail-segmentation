import { createServer, type Server } from 'node:http';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { wordingProblems, type NamedOutcome } from '../../src/shared/drift-wording';
import { driftAnswer, example } from '../support/drift';
import { shot } from '../support/evidence';

// Drift analysis (F28-07, #876), against a fake gateway that answers with the recorded examples of
// contracts/model-registry/. Besides what each screen shows, every screen is read back as text and
// checked against the wording rules of F23-07 (ADR-0065), so a sentence that a unit test cannot
// reach, such as a heading or a chart label, still has to follow them.

const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;

let server: Server;
let base = '';
let role = 'ANALYST';
let urls: string[] = [];
let hostile = false;
let broken: { path: string; file: string } | null = null;

/** Server text that is not markup but looks like it: it must reach the screen as text. */
const HOSTILE = '<img src=x onerror="window.xss=1">';
const cdata = (text: string): string => `<![CDATA[${text}]]>`;

/** What the one outcome-test claim of the examples names beside the phrase, written out here and not computed. */
const NAMED: NamedOutcome = {
  relation: 'Repurchase within 30 days, given the customer’s segment',
  horizon: '30 days',
  cohort: '2026-11-28',
};

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const reply = (status: number, body?: string): void => {
      res.writeHead(status, { 'Content-Type': 'application/xml' });
      res.end(body);
    };
    req.resume();
    req.on('end', () => {
      const url = new URL(req.url ?? '/', base);
      const path = url.pathname;
      urls.push(url.toString());
      if (req.method === 'POST' && path === '/api/auth/v1/sessions') return reply(201, tokens);
      if (path === '/api/auth/v1/sessions/current')
        return req.method === 'DELETE' ? reply(204) : reply(200, claims(role));
      if (broken !== null && path.endsWith(broken.path)) return reply(200, example(broken.file));
      const answer = driftAnswer(url);
      if (answer === undefined) return reply(404);
      if (!hostile) return reply(answer.status, answer.body);
      reply(answer.status, answer.body.replace(/<text>[^<]*<\/text>/, `<text>${cdata(HOSTILE)}</text>`));
    });
  });
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
test.afterAll(() => server.close());
test.beforeEach(() => {
  urls = [];
  hostile = false;
  broken = null;
});

async function launch(as: string): Promise<{ app: ElectronApplication; page: Page }> {
  role = as;
  const dir = mkdtempSync(join(tmpdir(), 'mosaiq-desktop-'));
  writeFileSync(join(dir, 'settings.json'), JSON.stringify({ baseUrl: base }));
  const app = await electron.launch({ args: ['.'], env: { ...process.env, MOSAIQ_DESKTOP_CONFIG_DIR: dir } });
  const page = await app.firstWindow();
  await page.waitForSelector('form');
  await page.fill('#f-email', 'a@b.c');
  await page.fill('#f-password', 'pw');
  await page.click('button[type=submit]');
  await expect(page.getByTestId('role')).toHaveText(`Signed in as ${as}`);
  return { app, page };
}

async function openModule(page: Page): Promise<void> {
  await page.locator('nav.nav button[data-view="drift"]').click();
  await expect(page.getByTestId('drift-reports-table')).toBeVisible();
}

const openReport = async (page: Page, id: number): Promise<void> => {
  await page.getByTestId(`open-report-${id}`).click();
  await expect(page.getByTestId('report-title')).toContainText(`Report #${id}`);
};

const openAlert = async (page: Page, id: number): Promise<void> => {
  await page.getByTestId('drift-tab-alerts').click();
  await page.getByTestId(`open-alert-${id}`).click();
  await expect(page.getByTestId('alert-title')).toContainText(`Alert #${id}`);
};

/** Every text a screen shows: the text of each element itself, and the names given to the charts. */
async function textsOf(page: Page, selector: string): Promise<string[]> {
  return page.locator(selector).evaluate((root) => {
    const out: string[] = [];
    const walk = (node: Element): void => {
      const own = Array.from(node.childNodes)
        .filter((c) => c.nodeType === Node.TEXT_NODE)
        .map((c) => c.textContent ?? '')
        .join('')
        .trim();
      if (own !== '') out.push(own);
      for (const name of ['aria-label', 'title']) {
        const value = node.getAttribute(name);
        if (value !== null && value !== '') out.push(value);
      }
      node.childNodes.forEach((c) => {
        if (c instanceof Element) walk(c);
      });
    };
    walk(root);
    return out;
  });
}

/** The screen follows F23-07: the phrase only on a result that names its evidence, and no word of behaviour across versions. */
async function followsWording(
  page: Page,
  selector: string,
  options: { claim?: boolean; acrossVersions?: boolean } = {},
): Promise<void> {
  const texts = await textsOf(page, selector);
  expect(texts.length).toBeGreaterThan(3);
  expect(
    wordingProblems(texts, {
      claims: options.claim === true ? [NAMED] : [],
      acrossVersions: options.acrossVersions === true,
    }),
  ).toEqual([]);
}

test('a report opens with the reference and current distribution of each variable, the statistic and the threshold', async () => {
  const { app, page } = await launch('ANALYST');
  await openModule(page);
  await expect(page.locator('[data-testid=drift-reports-table] tbody tr')).toHaveCount(6);
  await openReport(page, 32);
  await shot(page, '876-desktop-drift-01-report');

  await expect(page.getByTestId('report-version')).toHaveText('kmeans-k4-2026-10 (model version 5, KMEANS)');
  await expect(page.getByTestId('report-window')).toContainText('30 days ending 2026-12-06T03:00:00Z');
  await expect(page.getByTestId('report-window')).toContainText('2,400 customers');
  await expect(page.getByTestId('report-reference')).toContainText('18,432 customers, captured 2026-10-29');

  // One section per variable, each with its state, statistics, charts and bins.
  await expect(page.locator('[data-testid^=variable-][data-variable]')).toHaveCount(4);
  await expect(page.getByTestId('variable-state-SEGMENT_SHARES')).toContainText('Alarm.');
  await expect(page.getByTestId('variable-state-F')).toContainText('Watch.');
  await expect(page.getByTestId('variable-state-M')).toContainText('Normal.');

  // The statistic and the threshold it is judged by, for the PSI and for the chi-square.
  const psi = page.locator('[data-testid=statistics-SEGMENT_SHARES] tbody tr').first();
  await expect(psi).toContainText('Population stability index (PSI)');
  await expect(psi).toContainText('0.3148');
  await expect(psi).toContainText('at or above 0.1');
  await expect(psi).toContainText('at or above 0.25');
  await expect(psi).toContainText('Alarm');
  const chi = page.locator('[data-testid=statistics-SEGMENT_SHARES] tbody tr').nth(1);
  await expect(chi).toContainText('676.3754');
  await expect(chi).toContainText('2.78e-146');
  await expect(chi).toContainText('p below 0.05');
  await expect(chi).toContainText('p below 0.01');
  await expect(page.getByTestId('meter-SEGMENT_SHARES-PSI')).toBeVisible();
  await expect(page.getByTestId('meter-SEGMENT_SHARES-CHI_SQUARE')).toHaveCount(0);

  // Both distributions are charted: a pair of columns for each bin, and a legend naming the two series.
  const chart = page.getByTestId('chart-R');
  await expect(chart.locator('li')).toHaveText(['Reference', 'Current window']);
  await expect(chart.locator('g.viz-pair')).toHaveCount(5);
  await expect(chart.locator('path.viz-ref')).toHaveCount(4);
  await expect(chart.locator('path.viz-cur')).toHaveCount(4);
  await expect(page.getByTestId('chart-SEGMENT_SHARES').locator('g.viz-pair')).toHaveCount(4);
  await expect(page.getByTestId('chart-SEGMENT_SHARES').locator('g.viz-pair').first()).toHaveAttribute(
    'aria-label',
    'CHAMPIONS: Reference 12.0%, Current window 4.0%',
  );
  // The same figures are in the table beside it, and in the readout on focus.
  await expect(page.locator('[data-testid=bins-R] tbody tr').first()).toContainText('31.0%');
  await expect(page.locator('[data-testid=bins-R] tbody tr').first()).toContainText('14.0%');
  await chart.locator('g.viz-pair').first().focus();
  await expect(page.getByTestId('chart-R-readout')).toHaveText(
    'B1 (0 to 30): Reference 31.0%, Current window 14.0%',
  );

  // It names the alerts it raised and the report before it.
  await expect(page.getByTestId('report-alerts')).toContainText('Alert #41');
  await expect(page.getByTestId('report-previous')).toContainText('Report #31');
  expect(urls.some((u) => u.endsWith('/api/model-registry/v1/drift-reports/32'))).toBe(true);

  await followsWording(page, '[data-testid=drift-report]');
  await expect(page.getByTestId('drift-report')).not.toContainText(/concept drift/i);
  await app.close();
});

test('a report with no number says why and shows no statistic, and a version change is never read as what customers did', async () => {
  const { app, page } = await launch('ANALYST');
  await openModule(page);

  await openReport(page, 29);
  await expect(page.getByTestId('report-insufficient_data')).toHaveText(
    'Insufficient data: the window holds 187 customers and a comparison needs at least 200. No figure is given.',
  );
  await expect(page.locator('[data-testid^=variable-][data-variable]')).toHaveCount(0);
  await expect(page.getByTestId('drift-report').locator('svg')).toHaveCount(0);
  await followsWording(page, '[data-testid=drift-report]');

  await openReport(page, 7);
  await shot(page, '876-desktop-drift-02-not-compared');
  const notice = page.getByTestId('report-not_compared');
  await expect(notice).toContainText('the window crosses a change of model version');
  await expect(notice).toContainText('A change of version is a methodological difference');
  await expect(notice).toContainText('nothing in this window is compared and no figure is given');
  await expect(page.getByTestId('report-comparison')).toHaveText('Not compared (change of version)');
  await expect(page.locator('[data-testid^=variable-][data-variable]')).toHaveCount(0);
  await expect(page.getByTestId('drift-report').locator('svg')).toHaveCount(0);
  await expect(page.getByTestId('drift-report').locator('table')).toHaveCount(0);
  await followsWording(page, '[data-testid=drift-report]', { acrossVersions: true });

  // The list says the same in one cell.
  await expect(page.locator('[data-report-id="7"]')).toContainText('Not compared (change of version)');
  await app.close();
});

test('the outcome test is the only screen that says concept drift, and it names its relation, horizon and cohort', async () => {
  const { app, page } = await launch('AUDITOR');
  await openModule(page);
  // A list never says it: the report that holds a claim is a row like the others.
  await expect(page.getByTestId('drift-reports-list')).not.toContainText(/concept drift/i);
  await followsWording(page, '[data-testid=drift-reports-list]');

  await openReport(page, 36);
  await shot(page, '876-desktop-drift-03-outcome-test');
  const claim = page.getByTestId('outcome-claim');
  await expect(claim).toContainText('Concept drift');
  await expect(claim).toContainText('relation: Repurchase within 30 days, given the customer’s segment');
  await expect(claim).toContainText('horizon: 30 days');
  await expect(claim).toContainText('cohort as of 2026-11-28');
  await expect(claim).toContainText('LOYAL');
  await expect(page.getByTestId('outcome-relation')).toHaveText(
    'Repurchase within 30 days, given the customer’s segment',
  );
  await expect(page.getByTestId('outcome-thresholds')).toContainText('at least 100 customers on each side');
  const loyal = page.locator('[data-testid=segments-table] tr[data-segment=LOYAL]');
  await expect(loyal).toContainText('-9.95');
  await expect(loyal).toContainText('1.26e-25');
  await expect(loyal).toContainText('Passed the test');
  await expect(page.getByTestId('chart-rates').locator('g.viz-pair')).toHaveCount(4);
  await expect(page.getByTestId('chart-rates').locator('li')).toHaveText([
    'Reference cohort',
    'Current cohort',
  ]);
  await followsWording(page, '[data-testid=drift-report]', { claim: true });

  // A first pass of one segment claims nothing.
  await page.getByTestId('previous-report').click();
  await expect(page.getByTestId('report-title')).toContainText('Report #21');
  await expect(page.getByTestId('outcome-claim')).toHaveCount(0);
  await expect(page.getByTestId('outcome-verdict')).toContainText(
    'LOYAL passed the test in this cohort. Nothing is claimed until a segment passes in two consecutive matured cohorts.',
  );
  await expect(page.locator('[data-testid=segments-table] tr[data-segment=LOYAL]')).toContainText(
    'first time',
  );
  await expect(page.getByTestId('drift-report')).not.toContainText(/concept drift/i);
  await followsWording(page, '[data-testid=drift-report]');
  await app.close();
});

test('an alert opens with its explanation: the change, the version and the suggested review', async () => {
  const { app, page } = await launch('ANALYST');
  await openModule(page);
  await page.getByTestId('drift-tab-alerts').click();
  await expect(page.locator('[data-testid=drift-alerts-table] tbody tr')).toHaveCount(3);
  // The list names the finding; a distribution alert is a distribution change, the outcome-test alert names its evidence.
  await expect(page.locator('[data-alert-id="41"]')).toContainText('Distribution change');
  await expect(page.locator('[data-alert-id="43"]')).toContainText(
    'Concept drift (relation: Repurchase within 30 days',
  );
  await followsWording(page, '[data-testid=drift-alerts-list]', { claim: true });

  await openAlert(page, 41);
  await shot(page, '876-desktop-drift-04-alert');
  await expect(page.getByTestId('alert-change')).toContainText(
    'Distribution change: the share of customers in each segment differs from the reference of model version 5.',
  );
  await expect(page.getByTestId('alert-change')).toContainText(
    'does not show that the segments stopped predicting anything',
  );
  await expect(page.getByTestId('alert-variable')).toHaveText('Segment shares');
  await expect(page.getByTestId('alert-version')).toHaveText('kmeans-k4-2026-10 (model version 5, KMEANS)');
  await expect(page.getByTestId('alert-review')).toContainText('Review the segment mix.');
  await expect(page.getByTestId('alert-review')).toContainText(
    'Check whether its frozen scoring still fits the current customers',
  );
  await expect(page.getByTestId('alert-explanation')).toContainText('A suggestion to review, not a verdict.');
  // How much: the statistics against their thresholds and the bins that moved most, charted.
  await expect(page.locator('[data-testid=statistics-alert] tbody tr')).toHaveCount(2);
  await expect(page.locator('[data-testid=statistics-alert] tbody tr').first()).toContainText('0.3148');
  await expect(page.locator('[data-testid=statistics-alert] tbody tr').first()).toContainText(
    'at or above 0.25',
  );
  await expect(page.getByTestId('chart-alert').locator('g.viz-pair')).toHaveCount(3);
  await expect(page.getByTestId('chart-alert')).toContainText('the 3 bins that moved most');
  await expect(page.locator('[data-testid=bins-alert] tbody tr').first()).toContainText('LOYAL');
  await followsWording(page, '[data-testid=drift-alert]');
  await expect(page.getByTestId('drift-alert')).not.toContainText(/concept drift/i);

  await openAlert(page, 42);
  await expect(page.getByTestId('alert-variable')).toHaveText('Recency (R)');
  await expect(page.getByTestId('alert-review')).toContainText('Review the variable’s distribution.');
  await expect(page.getByTestId('chart-alert').locator('g.viz-pair')).toHaveCount(3);
  await followsWording(page, '[data-testid=drift-alert]');

  await openAlert(page, 43);
  await shot(page, '876-desktop-drift-05-alert-outcome-test');
  await expect(page.getByTestId('alert-change')).toContainText(
    'Concept drift (relation: Repurchase within 30 days',
  );
  await expect(page.getByTestId('alert-change')).toContainText('horizon: 30 days');
  await expect(page.getByTestId('alert-change')).toContainText('cohort as of 2026-11-28');
  await expect(page.getByTestId('alert-review')).toContainText('Review what the segments predict.');
  await expect(page.getByTestId('alert-variable')).toHaveText('Repurchase rate');
  await expect(page.locator('[data-testid=segments-table] tbody tr')).toHaveCount(1);
  await expect(page.getByTestId('statistics-alert')).toHaveCount(0);
  await followsWording(page, '[data-testid=drift-alert]', { claim: true });

  // The report that raised an alert opens from it, and so does the report before.
  await page.getByTestId('alert-report').click();
  await expect(page.getByTestId('drift-reports-panel')).toBeVisible();
  await expect(page.getByTestId('report-title')).toContainText('Report #36');
  await expect(page.getByTestId('report-alerts')).toContainText('Alert #43');
  await page.getByTestId('report-alert-43').click();
  await expect(page.getByTestId('alert-title')).toContainText('Alert #43');
  await app.close();
});

test('the filters are sent to the service and narrow the lists', async () => {
  const { app, page } = await launch('MARKETING');
  await openModule(page);
  await page.selectOption('[data-testid=drift-report-filters] select >> nth=0', 'OUTCOME_TEST');
  await page.getByRole('button', { name: 'Search reports' }).click();
  await expect(page.locator('[data-testid=drift-reports-table] tbody tr')).toHaveCount(2);
  await expect(page.locator('[data-testid=drift-reports-table] tbody tr td:nth-child(1)')).toHaveText([
    '#36',
    '#21',
  ]);
  expect(urls.some((u) => u.includes('/drift-reports?') && u.includes('kind=OUTCOME_TEST'))).toBe(true);

  await page.selectOption('[data-testid=drift-report-filters] select >> nth=0', '');
  await page.selectOption('[data-testid=drift-report-filters] select >> nth=1', 'INSUFFICIENT_DATA');
  await page.getByRole('button', { name: 'Search reports' }).click();
  await expect(page.locator('[data-testid=drift-reports-table] tbody tr')).toHaveCount(1);
  await expect(page.locator('[data-report-id="29"]')).toBeVisible();

  await page.fill('[data-testid=drift-report-filters] input', '9');
  await page.selectOption('[data-testid=drift-report-filters] select >> nth=1', '');
  await page.getByRole('button', { name: 'Search reports' }).click();
  await expect(page.getByTestId('reports-empty')).toHaveText('No drift report matches these filters.');
  expect(urls.some((u) => u.includes('model_version_id=9'))).toBe(true);

  await page.getByTestId('drift-tab-alerts').click();
  await page.selectOption('[data-testid=drift-alert-filters] select >> nth=1', 'R');
  await page.getByRole('button', { name: 'Search alerts' }).click();
  await expect(page.locator('[data-testid=drift-alerts-table] tbody tr')).toHaveCount(1);
  await expect(page.locator('[data-alert-id="42"]')).toBeVisible();
  expect(urls.some((u) => u.includes('/drift-alerts?') && u.includes('variable=R'))).toBe(true);
  await app.close();
});

test('the module follows the role, and the main process refuses a forced call', async () => {
  let { app, page } = await launch('STORE_MANAGER');
  await expect(page.locator('nav.nav button[data-view="drift"]')).toHaveCount(0);
  const urlsBefore = urls.length;
  const refused = await page.evaluate(async () => [
    await window.mosaiq.listDriftReports({}),
    await window.mosaiq.getDriftReport(32),
    await window.mosaiq.listDriftAlerts({}),
    await window.mosaiq.getDriftAlert(41),
  ]);
  for (const r of refused) expect(r).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  expect(urls.slice(urlsBefore).filter((u) => u.includes('/drift-'))).toEqual([]);
  await app.close();

  ({ app, page } = await launch('INVENTORY_PLANNER'));
  await expect(page.locator('nav.nav button[data-view="drift"]')).toHaveCount(0);
  await app.close();

  ({ app, page } = await launch('AUDITOR'));
  await openModule(page);
  // The shell's export is offered, on the table the module shows first.
  await expect(page.getByTestId('export')).toBeVisible();
  const missing = await page.evaluate(async () => [
    await window.mosaiq.getDriftReport(999),
    await window.mosaiq.getDriftAlert(999),
  ]);
  expect(missing[0]).toMatchObject({ ok: false, error: { code: 'drift_report_not_found' } });
  expect(missing[1]).toMatchObject({ ok: false, error: { code: 'drift_alert_not_found' } });
  await app.close();
});

test('an answer outside the contract is refused and nothing of it is drawn', async () => {
  const { app, page } = await launch('ANALYST');
  await openModule(page);
  broken = { path: '/drift-reports/32', file: 'drift-report-with-unknown-variable.invalid' };
  await page.getByTestId('open-report-32').click();
  await expect(page.getByTestId('error-code')).toHaveText('contract_defect');
  await expect(page.getByTestId('drift-report').locator('svg')).toHaveCount(0);
  await expect(page.getByTestId('drift-report')).not.toContainText('SEGMENT_SHARES');

  broken = { path: '/drift-alerts/41', file: 'drift-alert-with-unknown-claim.invalid' };
  await page.getByTestId('drift-tab-alerts').click();
  await page.getByTestId('open-alert-41').click();
  await expect(page.getByTestId('drift-alert').getByTestId('error-code')).toHaveText('contract_defect');
  await expect(page.getByTestId('alert-explanation')).toHaveCount(0);
  await app.close();
});

test('text from the service reaches the screen as text, and the charts run under the CSP', async () => {
  hostile = true;
  const { app, page } = await launch('ANALYST');
  const violations: string[] = [];
  page.on('console', (m) => {
    if (/content security policy|refused to/i.test(m.text())) violations.push(m.text());
  });
  await openModule(page);
  await openAlert(page, 41);
  await expect(page.getByTestId('alert-review')).toContainText(HOSTILE);
  expect(await page.evaluate(() => 'xss' in window)).toBe(false);
  await expect(page.getByTestId('alert-review').locator('img')).toHaveCount(0);

  await page.getByTestId('drift-tab-reports').click();
  await openReport(page, 32);
  // The charts are SVG built by the script: no inline style, no foreign object, nothing loaded from elsewhere.
  expect(await page.locator('[data-testid=drift-report] [style]').count()).toBe(0);
  expect(
    await page.locator('[data-testid=drift-report] foreignObject, [data-testid=drift-report] script').count(),
  ).toBe(0);
  expect(await page.locator('[data-testid=drift-report] svg').count()).toBe(8);
  expect(violations).toEqual([]);
  await app.close();
});

test('the charts sit in their own scroll area in a narrow window and never scroll the page sideways', async () => {
  const { app, page } = await launch('ANALYST');
  await openModule(page);
  await openReport(page, 32);
  for (const [width, height] of [
    [375, 900],
    [1440, 900],
  ] as const) {
    await app.evaluate(
      ({ BrowserWindow }, size) => {
        BrowserWindow.getAllWindows()[0]?.setSize(size[0], size[1]);
      },
      [width, height],
    );
    await expect.poll(async () => page.evaluate(() => window.innerWidth)).toBeLessThanOrEqual(width);
    const fits = await page.evaluate(() => {
      const chart = document.querySelector('[data-testid=chart-SEGMENT_SHARES] .scroll') as HTMLElement;
      return {
        page: document.documentElement.scrollWidth <= window.innerWidth + 1,
        chart: chart.getBoundingClientRect().right <= window.innerWidth + 1,
      };
    });
    expect(fits).toEqual({ page: true, chart: true });
    await page.getByTestId('chart-SEGMENT_SHARES').scrollIntoViewIfNeeded();
    await shot(page, `876-desktop-drift-06-${width}px`);
  }
  await app.close();
});
