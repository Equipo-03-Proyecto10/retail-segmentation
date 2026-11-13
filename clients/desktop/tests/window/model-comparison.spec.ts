import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';

// The comparison module against XML examples and XSD-valid assignment pages from the two services
// it composes. No request value is echoed into HTML or into a fake response.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const REGISTRY = 'https://mosaiq.maxthecoder.online/xml/model-registry/v1';
const SEGMENTS = 'https://mosaiq.maxthecoder.online/xml/segments/v1';
const COMMON = 'https://mosaiq.maxthecoder.online/xml/common/v1';
const example = (name: string): string =>
  readFileSync(join(CONTRACTS, 'model-registry', 'examples', `${name}.xml`), 'utf8');
const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>ANALYST</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2099-01-01T00:00:00Z</expires_at></SessionClaims>`;

const versions = Object.fromEntries(
  (example('model-versions-page').match(/<ModelVersion>[\s\S]*?<\/ModelVersion>/g) ?? [])
    .map((block) => [Number(/<version_id>(\d+)<\/version_id>/.exec(block)?.[1]), block])
    .filter(([id]) => id === 3 || id === 5),
) as Record<number, string>;

const customers = {
  same: '11111111-1111-4111-8111-111111111111',
  changed: '22222222-2222-4222-8222-222222222222',
  unassigned: '33333333-3333-4333-8333-333333333333',
  rulesOnly: '44444444-4444-4444-8444-444444444444',
  clustersOnly: '55555555-5555-4555-8555-555555555555',
};

function assignment(customer: string, version: number, label?: string): string {
  return `<VersionedSegmentAssignment><customer_id>${customer}</customer_id><run_id>${version}</run_id><model_version_id>${version}</model_version_id>${label === undefined ? '' : `<label_code>${label}</label_code>`}<valid_from>2026-10-15T08:10:00Z</valid_from></VersionedSegmentAssignment>`;
}

function assignmentPage(version: number): string {
  const rows =
    version === 3
      ? [
          assignment(customers.same, 3, 'LOYAL'),
          assignment(customers.changed, 3, 'POTENTIAL'),
          assignment(customers.unassigned, 3),
          assignment(customers.rulesOnly, 3, 'LOST'),
        ]
      : [
          assignment(customers.same, 5, 'LOYAL'),
          assignment(customers.changed, 5, 'AT_RISK'),
          assignment(customers.unassigned, 5),
          assignment(customers.clustersOnly, 5, 'CHAMPION'),
        ];
  return `<VersionedSegmentAssignments xmlns="${SEGMENTS}" xmlns:common="${COMMON}">${rows.join('')}<common:PageInfo><common:page>1</common:page><common:size>100</common:size><common:total>${rows.length}</common:total></common:PageInfo></VersionedSegmentAssignments>`;
}

let server: Server;
let base = '';
const comparedRequests: string[] = [];

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const reply = (status: number, body?: string): void => {
      res.writeHead(status, { 'Content-Type': 'application/xml', 'X-Content-Type-Options': 'nosniff' });
      res.end(body);
    };
    req.resume();
    req.on('end', () => {
      const url = new URL(req.url ?? '/', base);
      const path = url.pathname;
      if (!path.startsWith('/api/auth/'))
        comparedRequests.push(`${req.method ?? 'GET'} ${path}${url.search}`);
      if (req.method === 'POST' && path === '/api/auth/v1/sessions') return reply(201, tokens);
      if (path === '/api/auth/v1/sessions/current')
        return req.method === 'DELETE' ? reply(204) : reply(200, claims);
      if (path === '/api/model-registry/v1/model-versions')
        return reply(
          200,
          `<ModelVersions xmlns="${REGISTRY}" xmlns:common="${COMMON}">${versions[5]}${versions[3]}<common:PageInfo><common:page>1</common:page><common:size>100</common:size><common:total>2</common:total></common:PageInfo></ModelVersions>`,
        );
      const detail = /^\/api\/model-registry\/v1\/model-versions\/(3|5)$/.exec(path);
      if (detail) {
        const id = Number(detail[1]);
        return reply(200, versions[id]?.replace('<ModelVersion>', `<ModelVersion xmlns="${REGISTRY}">`));
      }
      if (path === '/api/segments/v1/segment-assignments/versioned')
        return reply(200, assignmentPage(Number(url.searchParams.get('model_version_id'))));
      return reply(404);
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});

test.afterAll(() => server.close());
test.beforeEach(() => {
  comparedRequests.length = 0;
});

async function launch(width: number): Promise<{ app: ElectronApplication; page: Page }> {
  const dir = mkdtempSync(join(tmpdir(), 'mosaiq-desktop-'));
  writeFileSync(join(dir, 'settings.json'), JSON.stringify({ baseUrl: base }));
  const app = await electron.launch({
    args: ['.'],
    env: { ...process.env, MOSAIQ_DESKTOP_CONFIG_DIR: dir },
  });
  const page = await app.firstWindow();
  await page.setViewportSize({ width, height: 900 });
  await page.fill('#f-email', 'a@b.c');
  await page.fill('#f-password', 'pw');
  await page.click('button[type=submit]');
  await expect(page.getByTestId('role')).toHaveText('Signed in as ANALYST');
  return { app, page };
}

for (const width of [375, 1440]) {
  test(`shows the F23-06 criteria and keeps agreement verdict-free at ${width}px`, async () => {
    const { app, page } = await launch(width);
    await page.locator('nav.nav button[data-view="model-comparison"]').click();
    await expect(page.getByTestId('comparison-submit')).toBeEnabled();
    await page.getByTestId('comparison-submit').click();

    const criteria = page.getByTestId('comparison-criteria');
    await expect(criteria.locator('tbody tr')).toHaveCount(3);
    await expect(criteria.locator('tr', { hasText: 'Interpretability' })).toContainText('0.20');
    await expect(criteria.locator('tr', { hasText: 'Stability' })).toContainText('0.10');
    await expect(criteria.locator('tr', { hasText: 'Usefulness for decisions' })).toContainText('0.05');
    await expect(criteria.locator('tr', { hasText: 'Stability' })).toContainText('0.93');
    await expect(criteria).toContainText('Not recorded by model-registry');

    await expect(page.getByTestId('methodological-note')).toContainText('not a change in customer behaviour');
    await expect(page.getByTestId('agreement-warning')).toContainText('not a quality criterion');
    await expect(page.getByTestId('agreement-context')).toContainText(
      '3 customers were scored by both: 2 agreed and 1 disagreed',
    );
    await expect(page.getByTestId('agreement-context')).toContainText('1 were scored only by');
    await expect(page.getByTestId('comparison-cross-tab').locator('tbody tr')).toHaveCount(3);
    await expect(page.getByTestId('model-comparison-result')).not.toContainText(
      /favours|winner|outperformed/i,
    );
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth),
    ).toBe(true);
    expect(comparedRequests.filter((request) => request.includes('model-versions/')).length).toBeGreaterThan(
      1,
    );
    expect(
      comparedRequests.filter((request) => request.includes('segment-assignments/versioned')),
    ).toHaveLength(2);
    expect(comparedRequests.every((request) => request.startsWith('GET '))).toBe(true);
    await app.close();
  });
}
