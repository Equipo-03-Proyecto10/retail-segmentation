import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { shot } from '../support/evidence';

// The K-means configuration module (F28-05, #874), against a fake gateway that answers with the
// recorded examples of contracts/model-registry/. model-registry is not implemented yet, so the
// fake keeps just what the screen needs to see change: each version's state, and how many times
// the running training has been polled. It echoes nothing from a request into a response.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const REGISTRY = 'https://mosaiq.maxthecoder.online/xml/model-registry/v1';
const COMMON = 'https://mosaiq.maxthecoder.online/xml/common/v1';
const example = (name: string): string =>
  readFileSync(join(CONTRACTS, 'model-registry', 'examples', `${name}.xml`), 'utf8');
/** The leaf elements of a request body, in order, to compare a sent request with a recorded one. */
const fieldsOf = (xml: string): [string, string][] =>
  [...xml.matchAll(/<([a-z_]+)>([^<]*)<\/\1>/g)].map((m) => [m[1] as string, m[2] as string]);

const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;

/** The three versions of the examples: 6 is a draft, 5 is published, 3 is a retired rule-based one. */
const BLOCKS: Record<number, string> = Object.fromEntries(
  (example('model-versions-page').match(/<ModelVersion>[\s\S]*?<\/ModelVersion>/g) ?? []).map((b) => [
    Number(/<version_id>(\d+)<\/version_id>/.exec(b)?.[1]),
    b,
  ]),
);
// The draft is made to differ from the published version, as a real retrain would.
BLOCKS[6] = (BLOCKS[6] ?? '')
  .replace('<inertia>412.78</inertia>', '<inertia>358.12</inertia>')
  .replace('<silhouette>0.41</silhouette>', '<silhouette>0.37</silhouette>')
  .replace('<r>0.1204</r>', '<r>0.15</r>');
const TRAINING_BLOCKS = example('trainings-page').match(/<ModelTraining>[\s\S]*?<\/ModelTraining>/g) ?? [];

const pageInfo = (total: number): string =>
  `<common:PageInfo><common:page>1</common:page><common:size>20</common:size><common:total>${total}</common:total></common:PageInfo>`;

interface Seen {
  method: string;
  url: string;
  body: string;
}

let server: Server;
let base = '';
let role = 'ADMIN';
let seen: Seen[] = [];
let states: Record<number, string> = {};
let polls = 0;
/** The poll on which the running training completes; never, unless a test says so. */
let completeOn = Number.POSITIVE_INFINITY;
let busy = false;
let publicationRefusal: string | null = null;

function reset(): void {
  seen = [];
  states = { 6: 'DRAFT', 5: 'PUBLISHED', 3: 'RETIRED' };
  polls = 0;
  completeOn = Number.POSITIVE_INFINITY;
  busy = false;
  publicationRefusal = null;
}

const block = (id: number): string =>
  (BLOCKS[id] ?? '').replace(/<state>\w+<\/state>/, `<state>${states[id]}</state>`);

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const reply = (status: number, body?: string): void => {
      res.writeHead(status, { 'Content-Type': 'application/xml', 'X-Content-Type-Options': 'nosniff' });
      res.end(body);
    };
    let body = '';
    req.on('data', (c: Buffer) => (body += c.toString()));
    req.on('end', () => {
      const url = new URL(req.url ?? '/', base);
      const path = url.pathname;
      const method = req.method ?? 'GET';
      seen.push({ method, url: url.toString(), body });
      if (method === 'POST' && path === '/api/auth/v1/sessions') return reply(201, tokens);
      if (path === '/api/auth/v1/sessions/current')
        return method === 'DELETE' ? reply(204) : reply(200, claims(role));
      if (!path.startsWith('/api/model-registry/v1/')) return reply(404);
      const rest = path.slice('/api/model-registry/v1'.length);
      if (method !== 'GET' && role !== 'ADMIN') return reply(403, example('forbidden-role'));

      if (rest === '/model-versions' && method === 'GET') {
        const wanted = [...Object.keys(BLOCKS)]
          .map(Number)
          .sort((a, b) => b - a)
          .filter((id) => {
            const state = url.searchParams.get('state');
            const m = url.searchParams.get('method');
            return (
              (state === null || states[id] === state) && (m === null || (m === 'RFM_RULES') === (id === 3))
            );
          });
        return reply(
          200,
          `<ModelVersions xmlns="${REGISTRY}" xmlns:common="${COMMON}">${wanted.map(block).join('')}${pageInfo(wanted.length)}</ModelVersions>`,
        );
      }
      const version = /^\/model-versions\/(\d+)$/.exec(rest);
      if (version && method === 'GET') {
        const id = Number(version[1]);
        if (BLOCKS[id] === undefined) return reply(404, example('model-version-not-found'));
        return reply(200, block(id).replace('<ModelVersion>', `<ModelVersion xmlns="${REGISTRY}">`));
      }
      const publication = /^\/model-versions\/(\d+)\/publications$/.exec(rest);
      if (publication && method === 'POST') {
        if (publicationRefusal !== null) return reply(409, example(publicationRefusal));
        for (const id of Object.keys(states).map(Number))
          if (states[id] === 'PUBLISHED') states[id] = 'RETIRED';
        states[Number(publication[1])] = 'PUBLISHED';
        return reply(201, example('publication-published'));
      }
      if (rest === '/model-trainings' && method === 'POST')
        return busy ? reply(409, example('training-in-progress')) : reply(201, example('training-running'));
      if (rest === '/model-trainings' && method === 'GET') {
        const running = url.searchParams.get('state') === 'RUNNING' && busy;
        return reply(
          200,
          `<ModelTrainings xmlns="${REGISTRY}" xmlns:common="${COMMON}">${running ? (TRAINING_BLOCKS[0] ?? '') : ''}${pageInfo(running ? 1 : 0)}</ModelTrainings>`,
        );
      }
      if (rest === '/model-trainings/9' && method === 'GET') {
        polls += 1;
        return reply(200, example(polls >= completeOn ? 'training-completed' : 'training-running'));
      }
      reply(404);
    });
  });
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
test.afterAll(() => server.close());
test.beforeEach(reset);

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

const nav = (page: Page): Promise<string[]> =>
  page.locator('nav.nav button').evaluateAll((bs) => bs.map((b) => (b as HTMLElement).dataset.view ?? ''));
const open = async (page: Page): Promise<void> => {
  await page.locator('nav.nav button[data-view="kmeans"]').click();
  await expect(page.getByTestId('versions-table')).toBeVisible();
};
const posts = (): Seen[] => seen.filter((s) => s.method === 'POST' && s.url.includes('/model-'));

test('an administrator launches a training, follows its progress and finds the result as a draft', async () => {
  completeOn = 1;
  const { app, page } = await launch('ADMIN');
  await open(page);
  await expect(page.getByTestId('training-form')).toBeVisible();
  // The versions list is K-means only, newest first: the draft, then the published one.
  await expect(page.locator('[data-testid=versions-table] tbody tr')).toHaveCount(2);
  await expect(page.getByTestId('version-state-6')).toHaveText('DRAFT');
  await expect(page.getByTestId('version-state-5')).toHaveText('PUBLISHED');

  await page.fill('#train-version_name', 'kmeans-k5-trial-2');
  await page.fill('#train-k', '5');
  await page.fill('#train-seed', '77');
  await page.getByTestId('training-form').getByRole('button', { name: 'Start the training' }).click();

  await expect(page.getByTestId('training-state')).toHaveText('RUNNING');
  await expect(page.locator('[data-testid=training-progress] progress')).toBeVisible();
  await expect(page.getByTestId('training-seed')).toHaveText('77');
  await shot(page, '874-desktop-kmeans-01-training-running');
  const sent = posts().find((s) => s.url.endsWith('/model-trainings'));
  expect(fieldsOf(sent?.body ?? '')).toEqual(fieldsOf(example('training.request')));

  // The training ends: its result is stored as a draft version, which the screen opens.
  await expect(page.getByTestId('training-state')).toHaveText('COMPLETED', { timeout: 15000 });
  await expect(page.getByTestId('training-text')).toContainText('after 31 iterations, converged');
  await expect(page.getByTestId('training-inertia')).toHaveText('358.12');
  await expect(page.getByTestId('training-stored')).toContainText('stored as the draft version');
  await expect(page.getByTestId('version-detail')).toContainText('kmeans-k4-trial (#6)');
  await expect(page.getByTestId('version-state')).toHaveText('DRAFT');
  expect(polls).toBeGreaterThan(0);
  await shot(page, '874-desktop-kmeans-02-draft-stored');
  await app.close();
});

test('a refused launch names its field, and a training already running is followed', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page);
  await page.fill('#train-version_name', 'x');
  await page.fill('#train-k', '1');
  await page.getByTestId('training-form').getByRole('button', { name: 'Start the training' }).click();
  await expect(page.getByTestId('detail-k')).toHaveText('k: out_of_range');
  await page.fill('#train-k', '4');
  await page.getByTestId('feature-R').uncheck();
  await page.getByTestId('feature-F').uncheck();
  await page.getByTestId('feature-M').uncheck();
  await page.getByTestId('training-form').getByRole('button', { name: 'Start the training' }).click();
  await expect(page.getByTestId('detail-features')).toHaveText('features: required');
  // Nothing was sent for either.
  expect(posts()).toEqual([]);

  await page.getByTestId('feature-R').check();
  busy = true;
  await page.getByTestId('training-form').getByRole('button', { name: 'Start the training' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('training_in_progress');
  await expect(page.getByTestId('training-title')).toHaveText('Training #9: kmeans-k5-trial-2');
  await expect(page.getByTestId('training-state')).toHaveText('RUNNING');
  await app.close();
});

test('a training that is running when the screen opens is followed there', async () => {
  busy = true;
  const { app, page } = await launch('ANALYST');
  await open(page);
  await expect(page.getByTestId('training-state')).toHaveText('RUNNING');
  await expect(page.getByTestId('training-title')).toContainText('#9');
  await app.close();
});

test('two versions are compared side by side: sizes, centroids in raw units, inertia and silhouette', async () => {
  const { app, page } = await launch('ANALYST');
  await open(page);
  // A draft is compared with whatever is published; nothing is shown until it is asked for.
  await expect(page.getByTestId('compare-title')).toHaveCount(0);
  await page.getByTestId('view-version-6').click();
  await page.getByTestId('compare-published').click();
  await expect(page.getByTestId('compare-title')).toHaveText(
    'kmeans-k4-trial (#6) and kmeans-k4-2026-10 (#5)',
  );
  await page.getByTestId('tick-6').check();
  await expect(page.getByTestId('compare-selected')).toBeDisabled();
  await page.getByTestId('tick-5').check();
  await page.getByTestId('compare-selected').click();

  await expect(page.getByTestId('compare-title')).toHaveText(
    'kmeans-k4-trial (#6) and kmeans-k4-2026-10 (#5)',
  );
  const summary = page.getByTestId('compare-summary');
  await expect(summary.locator('tr', { hasText: 'Inertia' })).toContainText('358.12');
  await expect(summary.locator('tr', { hasText: 'Inertia' })).toContainText('412.78');
  await expect(summary.locator('tr', { hasText: 'Inertia' })).toContainText('Differs');
  await expect(summary.locator('tr', { hasText: 'Silhouette' })).toContainText('0.37');
  await expect(summary.locator('tr', { hasText: 'Silhouette' })).toContainText('0.41');
  await expect(summary.locator('tr', { hasText: 'Window' })).not.toContainText('Differs');
  // 2212 of 18432 customers under CHAMPIONS in both versions, and the other labels beside them.
  await expect(page.getByTestId('compare-sizes').locator('tbody tr')).toHaveCount(4);
  await expect(page.getByTestId('compare-sizes').locator('tr', { hasText: 'CHAMPIONS' })).toContainText(
    '2212 (12.0%)',
  );
  // CHAMPIONS recency: 0.15 * 180 = 27.0 days in the draft, 0.1204 * 180 = 21.7 in the published one.
  const champions = page.getByTestId('compare-centroids').locator('tbody tr', { hasText: 'CHAMPIONS' });
  await expect(champions.locator('td').nth(1)).toHaveText('27.0');
  await expect(champions.locator('td').nth(2)).toHaveText('34.3');
  await expect(champions.locator('td').nth(3)).toHaveText('7054.02');
  await expect(champions.locator('td').nth(4)).toHaveText('21.7');
  // The comparison is marked methodological and never ranks the versions.
  await expect(page.getByTestId('compare-notes')).toContainText('methodological comparison');
  await expect(page.getByTestId('compare-notes')).toContainText('does not say that either one is better');
  await shot(page, '874-desktop-kmeans-03-comparison');

  await app.close();
});

test('publishing a draft states what changes for customers before it is confirmed', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page);
  await page.getByTestId('view-version-6').click();
  await expect(page.getByTestId('version-state')).toHaveText('DRAFT');
  await expect(page.getByTestId('version-centroids').locator('tbody tr').first()).toContainText('27.0');
  await page.getByTestId('publish-open').click();

  const impact = page.getByTestId('publish-impact');
  await expect(impact).toContainText(
    '"kmeans-k4-trial" replaces "kmeans-k4-2026-10" (KMEANS), which becomes RETIRED',
  );
  await expect(impact).toContainText('Every customer is scored again under "kmeans-k4-trial" in a full run');
  await expect(impact).toContainText('one of 4 labels: CHAMPIONS, LOYAL, AT_RISK, LOST');
  await expect(impact).toContainText('VERSION_CHANGE');
  await expect(impact).toContainText('never counted as customer behaviour');
  await shot(page, '874-desktop-kmeans-04-publish-impact');
  // Nothing has been published: the statement came first.
  expect(posts()).toEqual([]);

  // The reason is required, and an empty one sends nothing.
  await page.getByTestId('confirm-publish').click();
  await expect(page.getByTestId('detail-reason')).toHaveText('reason: required');
  expect(posts()).toEqual([]);

  const reason = 'K-means k=4 separates the at-risk customers better than the rule set.';
  await page.fill('#publish-reason', reason);
  await page.getByTestId('confirm-publish').click();
  await expect(page.getByTestId('notice')).toContainText('"kmeans-k4-trial" (#6) is published');
  await expect(page.getByTestId('version-state')).toHaveText('PUBLISHED');
  await expect(page.getByTestId('version-state-6')).toHaveText('PUBLISHED');
  await expect(page.getByTestId('version-state-5')).toHaveText('RETIRED');
  await expect(page.getByTestId('publish-open')).toHaveCount(0);
  const sent = posts();
  expect(sent).toHaveLength(1);
  expect(sent[0]?.url).toMatch(/\/model-versions\/6\/publications$/);
  expect(fieldsOf(sent[0]?.body ?? '')).toEqual([['reason', reason]]);
  await app.close();
});

test('a publication the service refuses is shown as returned and the version is read again', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page);
  await page.getByTestId('view-version-6').click();
  await page.getByTestId('publish-open').click();
  await expect(page.getByTestId('publish-impact')).toBeVisible();
  publicationRefusal = 'publication-conflict';
  await page.fill('#publish-reason', 'Because.');
  await page.getByTestId('confirm-publish').click();
  await expect(page.getByTestId('error-code')).toHaveText('publication_conflict');
  await expect(page.getByTestId('publish-dialog')).not.toBeVisible();
  // It is still a draft: the screen shows what the service has, and offers to try again.
  await expect(page.getByTestId('version-state')).toHaveText('DRAFT');
  await expect(page.getByTestId('publish-open')).toBeVisible();
  await app.close();
});

test('a role that only reads sees the versions and compares them, with no way to train or publish', async () => {
  for (const as of ['ANALYST', 'MARKETING', 'AUDITOR']) {
    reset();
    const { app, page } = await launch(as);
    expect(await nav(page)).toContain('kmeans');
    await open(page);
    await expect(page.getByTestId('training-form')).toHaveCount(0);
    await expect(page.getByTestId('read-only-note')).toBeVisible();
    await page.getByTestId('view-version-6').click();
    await expect(page.getByTestId('version-state')).toHaveText('DRAFT');
    await expect(page.getByTestId('compare-published')).toBeVisible();
    await expect(page.getByTestId('publish-open')).toHaveCount(0);
    // Forced through the bridge, the write is refused in the main process, before the network.
    const train = await page.evaluate(() =>
      window.mosaiq.startTraining({
        version_name: 'forced',
        window_days: 180,
        k: 4,
        max_iterations: 100,
        tolerance: '0.0001',
        features: ['R', 'F', 'M'],
        normalization: 'MIN_MAX',
      }),
    );
    expect(train).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    const publish = await page.evaluate(() => window.mosaiq.publishModelVersion(6, 'Forced.'));
    expect(publish).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(posts()).toEqual([]);
    await app.close();
  }
});

test('a role that may not read model versions is not offered the module', async () => {
  const { app, page } = await launch('STORE_MANAGER');
  expect(await nav(page)).not.toContain('kmeans');
  const forced = await page.evaluate(() => window.mosaiq.listModelVersions({}));
  expect(forced).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  expect(seen.filter((s) => s.url.includes('/model-registry/'))).toEqual([]);
  await app.close();
});
