import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';

// Store locations and geofences (F16-07, #485), against a fake gateway that
// answers with the recorded examples of contracts/stores/ and keeps the
// locations it is given.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const STORES = 'https://mosaiq.maxthecoder.online/xml/stores/v1';
const COMMON = 'https://mosaiq.maxthecoder.online/xml/common/v1';

const example = (service: string, name: string): string =>
  readFileSync(join(CONTRACTS, service, 'examples', `${name}.xml`), 'utf8');

const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;

interface Store {
  id: number;
  name: string;
  location: { latitude: string; longitude: string; radius: string } | null;
}
const initial = (): Store[] => [
  {
    id: 1,
    name: 'Centro Historico',
    location: { latitude: '19.432608', longitude: '-99.133209', radius: '150' },
  },
  { id: 2, name: 'Coyoacan', location: null },
];

const storeBody = (s: Store): string =>
  `<store_id>${s.id}</store_id><name>${s.name}</name><city>Mexico City</city><state>CDMX</state><is_active>true</is_active>${
    s.location
      ? `<location><latitude>${s.location.latitude}</latitude><longitude>${s.location.longitude}</longitude><geofence_radius_m>${s.location.radius}</geofence_radius_m><set_at>2026-10-15T18:20:00Z</set_at></location>`
      : ''
  }`;
const oneStore = (s: Store): string => `<Store xmlns="${STORES}">${storeBody(s)}</Store>`;
const directory = (list: Store[]): string =>
  `<Stores xmlns="${STORES}" xmlns:common="${COMMON}">${list.map((s) => `<Store>${storeBody(s)}</Store>`).join('')}<common:PageInfo><common:page>1</common:page><common:size>20</common:size><common:total>${list.length}</common:total></common:PageInfo></Stores>`;

interface Seen {
  method: string;
  url: string;
  body: string;
}

let server: Server;
let base = '';
let role = 'ADMIN';
let seen: Seen[] = [];
let db: Store[] = initial();
let nextRefusal: { file: string; status: number } | null = null;

function reset(): void {
  seen = [];
  db = initial();
  nextRefusal = null;
}

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
      if (method !== 'GET' && role !== 'ADMIN') return reply(403, example('stores', 'forbidden-role'));
      if (method !== 'GET' && nextRefusal !== null) {
        const r = nextRefusal;
        nextRefusal = null;
        return reply(r.status, example('stores', r.file));
      }
      if (path === '/api/stores/v1/stores') return reply(200, directory(db));
      const match = /^\/api\/stores\/v1\/stores\/(\d+)(\/location)?$/.exec(path);
      const store = db.find((s) => s.id === Number(match?.[1]));
      if (!match || !store) return reply(404, example('stores', 'store-not-found'));
      if (!match[2]) return reply(200, oneStore(store));
      if (method === 'PUT') {
        const field = (name: string): string =>
          new RegExp(`<${name}>([^<]*)</${name}>`).exec(body)?.[1] ?? '';
        store.location = {
          latitude: field('latitude'),
          longitude: field('longitude'),
          radius: field('geofence_radius_m'),
        };
        return reply(200, oneStore(store));
      }
      store.location = null;
      return reply(204);
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

const open = (page: Page, name: string) => page.locator(`nav.nav button[data-view="${name}"]`).click();
const writes = (): Seen[] => seen.filter((s) => s.url.endsWith('/location'));

test('every staff role reads the directory; a store without a location is marked', async () => {
  const { app, page } = await launch('MARKETING');
  const requested: string[] = [];
  page.on('request', (r) => requested.push(r.url()));
  await open(page, 'stores');
  await expect(page.locator('[data-testid=stores-table] tbody tr')).toHaveCount(2);
  await expect(page.getByTestId('store-location-1')).toHaveText('19.432608, -99.133209 (geofence 150 m)');
  await expect(page.getByTestId('store-location-2')).toHaveText(
    'no location: check-ins here are recorded as store_location_unknown',
  );

  await page.getByTestId('view-store-2').click();
  await expect(page.getByTestId('store-name')).toHaveText('Coyoacan');
  await expect(page.getByTestId('store-no-location')).toContainText('store_location_unknown');
  await page.getByTestId('view-store-1').click();
  await expect(page.getByTestId('store-latitude')).toHaveText('19.432608');
  await expect(page.getByTestId('store-longitude')).toHaveText('-99.133209');
  await expect(page.getByTestId('store-radius')).toHaveText('150 m');
  expect(seen.some((s) => s.url.endsWith('/stores/1') && s.method === 'GET')).toBe(true);

  // Read-only: no control, and a forced write is refused by the service and shown as a message.
  await expect(page.getByTestId('location-form')).toHaveCount(0);
  await expect(page.getByTestId('remove-location')).toHaveCount(0);
  const forced = await page.evaluate(() =>
    window.mosaiq.setStoreLocation(1, { latitude: '1', longitude: '1', geofence_radius_m: '100' }),
  );
  expect(forced).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });

  // No map: coordinates are numbers, nothing remote is requested, and the page embeds nothing.
  expect(requested.filter((u) => !u.startsWith('file:'))).toEqual([]);
  expect(await page.locator('img, iframe, canvas, script[src^="http"], link[href^="http"]').count()).toBe(0);
  await app.close();
});

test('an ADMIN sets a location; the stored location shows after a reload', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page, 'stores');
  await page.getByTestId('view-store-2').click();
  await page.fill('[data-testid=location-form] #c-latitude', '19.3467');
  await page.fill('[data-testid=location-form] #c-longitude', '-99.1617');
  await page.fill('[data-testid=location-form] #c-geofence_radius_m', '120');
  await page.getByRole('button', { name: 'Save location' }).click();
  await expect(page.getByTestId('notice')).toHaveText('Location of Coyoacan saved.');
  await expect(page.getByTestId('store-latitude')).toHaveText('19.3467');
  const put = writes().at(-1)!;
  expect(put.method).toBe('PUT');
  expect(put.url).toContain('/stores/2/location');
  expect(put.body).toContain(
    '<latitude>19.3467</latitude><longitude>-99.1617</longitude><geofence_radius_m>120</geofence_radius_m>',
  );
  // The directory is read again, and it shows what the service stored.
  await expect(page.getByTestId('store-location-2')).toHaveText('19.3467, -99.1617 (geofence 120 m)');
  await app.close();
});

test('a value outside its bounds is refused before sending, naming the field', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page, 'stores');
  await page.getByTestId('view-store-2').click();
  const form = page.getByTestId('location-form');
  const save = async (lat: string, lon: string, radius: string): Promise<void> => {
    await form.locator('#c-latitude').fill(lat);
    await form.locator('#c-longitude').fill(lon);
    await form.locator('#c-geofence_radius_m').fill(radius);
    await page.getByRole('button', { name: 'Save location' }).click();
  };
  await save('91', '-99.1', '100');
  await expect(page.getByTestId('detail-latitude')).toHaveText('latitude: out_of_range');
  await save('19.3', '-180.5', '100');
  await expect(page.getByTestId('detail-longitude')).toHaveText('longitude: out_of_range');
  await save('19.3', '-99.1', '24');
  await expect(page.getByTestId('detail-geofence_radius_m')).toHaveText('geofence_radius_m: out_of_range');
  await save('19.3', '-99.1', '2001');
  await expect(page.getByTestId('detail-geofence_radius_m')).toHaveText('geofence_radius_m: out_of_range');
  expect(writes()).toHaveLength(0);

  // What the service answers to a forced request is shown with its details next to the field.
  nextRefusal = { file: 'invalid-body-longitude', status: 422 };
  await save('19.3', '-99.1', '100');
  await expect(page.getByTestId('error-code')).toHaveText('invalid_body');
  await expect(page.getByTestId('detail-longitude')).toHaveText('longitude: out_of_range');
  expect(writes()).toHaveLength(1);
  nextRefusal = { file: 'invalid-body-geofence-radius-m', status: 422 };
  await save('19.3', '-99.1', '100');
  await expect(page.getByTestId('detail-geofence_radius_m')).toHaveText('geofence_radius_m: out_of_range');
  await app.close();
});

test('an ADMIN removes a location after a confirmation; an unknown store is store_not_found', async () => {
  const { app, page } = await launch('ADMIN');
  await open(page, 'stores');
  await page.getByTestId('view-store-1').click();
  await page.getByTestId('remove-location').click();
  expect(writes()).toHaveLength(0);
  await page.getByTestId('confirm-remove-location').click();
  await expect(page.getByTestId('notice')).toHaveText('Location of Centro Historico removed.');
  await expect(page.getByTestId('store-no-location')).toContainText('store_location_unknown');
  await expect(page.getByTestId('store-location-1')).toContainText('no location');
  expect(writes().at(-1)!.method).toBe('DELETE');
  await expect(page.getByTestId('remove-location')).toHaveCount(0);

  const unknown = await page.evaluate(() => window.mosaiq.removeStoreLocation(9999));
  expect(unknown).toMatchObject({ ok: false, error: { code: 'store_not_found' } });
  const missing = await page.evaluate(() => window.mosaiq.getStore(9999));
  expect(missing).toMatchObject({ ok: false, error: { code: 'store_not_found' } });

  // The same refusal reaches the screen as the service returned it.
  await page.getByTestId('view-store-2').click();
  await page.fill('[data-testid=location-form] #c-latitude', '19.3');
  await page.fill('[data-testid=location-form] #c-longitude', '-99.1');
  await page.fill('[data-testid=location-form] #c-geofence_radius_m', '100');
  nextRefusal = { file: 'store-not-found', status: 404 };
  await page.getByRole('button', { name: 'Save location' }).click();
  await expect(page.getByTestId('error-code')).toHaveText('store_not_found');
  await expect(page.getByTestId('error-correlation')).toHaveText('corr-store-not-found');
  await app.close();
});
