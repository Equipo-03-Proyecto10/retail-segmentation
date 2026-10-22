import { createServer, type Server } from 'node:http';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron as electron, expect, test, type ElectronApplication, type Page } from '@playwright/test';
import { deferred, type Deferred } from '../support/deferred';
import { shot } from '../support/evidence';

// The products and categories screens (F16-02, #410), against a fake gateway
// that answers with the recorded examples in contracts/*/examples/.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const AUTH = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
const PRODUCTS = 'https://mosaiq.maxthecoder.online/xml/products/v1';
const CATEGORIES = 'https://mosaiq.maxthecoder.online/xml/categories/v1';
const COMMON = 'https://mosaiq.maxthecoder.online/xml/common/v1';

const example = (service: string, name: string): string =>
  readFileSync(join(CONTRACTS, service, 'examples', `${name}.xml`), 'utf8');

/** The <Product> or <Category> elements of a recorded document, without their namespace declaration. */
function blocks(xml: string, element: 'Product' | 'Category'): string[] {
  const bare = xml
    .replace(/<\?xml[^>]*\?>/, '')
    .replace(new RegExp(`<${element} xmlns="[^"]*">`, 'g'), `<${element}>`);
  return bare.match(new RegExp(`<${element}>[\\s\\S]*?</${element}>`, 'g')) ?? [];
}
const idOf = (block: string, element: string): number =>
  Number(new RegExp(`<${element}>(\\d+)<`).exec(block)?.[1]);
const page = (root: string, ns: string, items: string[]): string =>
  `<${root} xmlns="${ns}" xmlns:common="${COMMON}">${items.join('')}<common:PageInfo><common:page>1</common:page><common:size>100</common:size><common:total>${items.length}</common:total></common:PageInfo></${root}>`;
const single = (block: string, element: string, ns: string): string =>
  block.replace(`<${element}>`, `<${element} xmlns="${ns}">`);

const tokens = `<TokenResult xmlns="${AUTH}"><access_token>a</access_token><refresh_token>r</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>2099-01-01T00:00:00Z</expires_at></TokenResult>`;
const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;

interface Seen {
  method: string;
  url: string;
  body: string;
}

let server: Server;
let base = '';
let role = 'ADMIN';
let seen: Seen[] = [];
let products: string[] = [];
let categories: string[] = [];
/** The recorded refusal the next PATCH or DELETE answers with, when a test sets one. */
let nextRefusal: { file: string; status: number } | null = null;
let treeReads = 0;
let heldUnfilteredProducts: Deferred<void> | null = null;
let productReads = 0;

function reset(): void {
  seen = [];
  nextRefusal = null;
  treeReads = 0;
  heldUnfilteredProducts = null;
  productReads = 0;
  products = blocks(example('products', 'products'), 'Product');
  // Dairy (1) with Cheese (11) and Yogurt (12) below it.
  categories = [
    ...blocks(example('categories', 'get-root-category'), 'Category'),
    ...blocks(example('categories', 'list-categories'), 'Category'),
  ];
}

test.beforeAll(async () => {
  server = createServer((req, res) => {
    const reply = (status: number, body?: string): void => {
      res.writeHead(status, { 'Content-Type': 'application/xml' });
      res.end(body);
    };
    let body = '';
    req.on('data', (c: Buffer) => (body += c.toString()));
    req.on('end', () => {
      const url = new URL(req.url ?? '/', base);
      const path = url.pathname;
      const method = req.method ?? 'GET';
      if (path.startsWith('/api/products/') || path.startsWith('/api/categories/'))
        seen.push({ method, url: url.toString(), body });
      if (method === 'POST' && path === '/api/auth/v1/sessions') return reply(201, tokens);
      if (path === '/api/auth/v1/sessions/current')
        return method === 'DELETE' ? reply(204) : reply(200, claims(role));
      const write = method !== 'GET';
      if (write && role !== 'ADMIN') return reply(403, example('categories', 'forbidden-role'));
      if (write && nextRefusal !== null) {
        const r = nextRefusal;
        nextRefusal = null;
        return reply(r.status, example(r.file.startsWith('category') ? 'categories' : 'products', r.file));
      }

      // products-service
      if (path === '/api/products/v1/products' && method === 'GET') {
        productReads += 1;
        const response = page('Products', PRODUCTS, url.searchParams.get('q') === 'no-match' ? [] : products);
        if (heldUnfilteredProducts !== null && url.searchParams.get('q') !== 'no-match') {
          void heldUnfilteredProducts.promise.then(() => reply(200, response));
          return;
        }
        return reply(200, response);
      }
      if (path === '/api/products/v1/products' && method === 'POST') {
        if (body.includes('<sku>MOS-CAM-001</sku>')) return reply(409, example('products', 'sku-taken'));
        products.push(...blocks(example('products', 'create-product'), 'Product'));
        return reply(201, example('products', 'create-product'));
      }
      const product = /^\/api\/products\/v1\/products\/(\d+)$/.exec(path);
      if (product) {
        const found = products.find((p) => idOf(p, 'product_id') === Number(product[1]));
        if (found === undefined || Number(product[1]) === 1002)
          return reply(404, example('products', 'product-not-found'));
        if (method === 'GET') return reply(200, single(found, 'Product', PRODUCTS));
        let updated = found;
        for (const field of ['name', 'category_id', 'list_price', 'is_active']) {
          const value = new RegExp(`<${field}>([^<]*)</${field}>`).exec(body)?.[1];
          if (value !== undefined)
            updated = updated.replace(
              new RegExp(`<${field}>[^<]*</${field}>`),
              `<${field}>${value}</${field}>`,
            );
        }
        products = products.map((p) => (p === found ? updated : p));
        return reply(200, single(updated, 'Product', PRODUCTS));
      }

      // categories-service
      if (path === '/api/categories/v1/categories' && method === 'GET') {
        treeReads += 1;
        return reply(200, page('Categories', CATEGORIES, categories));
      }
      if (path === '/api/categories/v1/categories' && method === 'POST') {
        categories.push(...blocks(example('categories', 'create-category'), 'Category'));
        return reply(201, example('categories', 'create-category'));
      }
      const descendants = /^\/api\/categories\/v1\/categories\/(\d+)\/descendants$/.exec(path);
      if (descendants) return reply(200, example('categories', 'list-descendants'));
      const cat = /^\/api\/categories\/v1\/categories\/(\d+)$/.exec(path);
      if (cat) {
        const found = categories.find((c) => idOf(c, 'category_id') === Number(cat[1]));
        if (found === undefined) return reply(404, example('categories', 'category-not-found'));
        if (method === 'GET') return reply(200, single(found, 'Category', CATEGORIES));
        if (method === 'DELETE') {
          categories = categories.filter((c) => c !== found);
          return reply(204);
        }
        let updated = found;
        const name = /<name>([^<]*)<\/name>/.exec(body)?.[1];
        if (name !== undefined) updated = updated.replace(/<name>[^<]*<\/name>/, `<name>${name}</name>`);
        if (body.includes('xsi:nil="true"'))
          updated = updated.replace(/<parent_category_id>[^<]*<\/parent_category_id>/, '');
        categories = categories.map((c) => (c === found ? updated : c));
        return reply(200, single(updated, 'Category', CATEGORIES));
      }
      reply(404, example('categories', 'category-not-found'));
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

const rows = (page: Page) => page.locator('[data-testid=products-table] tbody tr');
const treeIds = (page: Page) =>
  page
    .locator('[data-testid=category-tree] li')
    .evaluateAll((items) => items.map((li) => (li as HTMLElement).dataset.categoryId));

test('lists, filters and shows products, naming categories from categories-service', async () => {
  const { app, page } = await launch('ADMIN');
  await expect(rows(page)).toHaveCount(2);
  await expect(rows(page).first()).toContainText('Aisle Camera');
  await expect(rows(page).first()).toContainText('Yogurt');
  expect(seen.some((s) => s.url.endsWith('/api/categories/v1/categories/12'))).toBe(true);
  await shot(page, '410-desktop-products-01-list-with-category-names');

  await page.fill('#filter-q', 'camera');
  await page.selectOption('#filter-active', 'true');
  await page.selectOption('#filter-size', '50');
  await page.getByRole('button', { name: 'Search' }).click();
  await expect.poll(() => seen.filter((s) => s.url.includes('/products?')).at(-1)?.url).toContain('q=camera');
  const last = new URL(seen.filter((s) => s.url.includes('/products?')).at(-1)!.url).searchParams;
  expect(Object.fromEntries(last)).toMatchObject({ q: 'camera', active: 'true', page: '1', size: '50' });

  await page.getByTestId('view-1001').click();
  await expect(page.getByTestId('product-sku')).toHaveText('MOS-CAM-001');
  await expect(page.getByTestId('product-category')).toHaveText('Yogurt');
  await shot(page, '410-desktop-products-02-product-detail');

  await page.getByTestId('view-1002').click();
  await expect(page.getByTestId('error-code')).toHaveText('product_not_found');
  await app.close();
});

test('products keeps a newer filtered page when the initial page answers late', async () => {
  heldUnfilteredProducts = deferred<void>();
  const { app, page } = await launch('ADMIN');
  // Signing in renders the home screen twice (the reply and the state event),
  // so the unfiltered page is read once per render. Every unfiltered read is
  // held, the one of the screen on display included, while the filter answers.
  await expect.poll(() => productReads).toBeGreaterThanOrEqual(1);
  await page.fill('#filter-q', 'no-match');
  await page.getByRole('button', { name: 'Search' }).click();
  await expect.poll(() => seen.some((s) => s.url.includes('q=no-match'))).toBe(true);
  await expect(page.getByTestId('products-table')).toBeVisible();
  await expect(rows(page)).toHaveCount(0);

  heldUnfilteredProducts.resolve();
  await expect
    .poll(() => seen.filter((s) => s.url.endsWith('/api/categories/v1/categories/12')).length)
    .toBeGreaterThan(0);
  for (let sample = 0; sample < 10; sample += 1) {
    await page.waitForTimeout(100);
    await expect(rows(page)).toHaveCount(0);
  }
  await app.close();
});

test('an ADMIN creates a product, and refusals are shown as returned', async () => {
  const { app, page } = await launch('ADMIN');
  const fill = async (sku: string, price: string): Promise<void> => {
    await page.getByTestId('new-product').click();
    await page.fill('#c-product_id', '1051');
    await page.fill('#c-sku', sku);
    await page.fill('#c-name', 'Counter Scanner');
    await page.selectOption('[data-testid=product-new] #c-category_id', '12');
    await page.fill('#c-list_price', price);
    await page.getByText('Create product').click();
  };

  await fill('MOS-SCN-051', '-1.00');
  await expect(page.getByTestId('detail-list_price')).toHaveText('list_price: invalid_format');
  expect(seen.some((s) => s.method === 'POST')).toBe(false);

  await fill('MOS-CAM-001', '89.00');
  await expect(page.getByTestId('error-code')).toHaveText('sku_taken');
  await expect(page.getByTestId('error-correlation')).toHaveText('corr-create-409a');
  await shot(page, '410-desktop-products-03-sku-taken-refusal');

  await fill('MOS-SCN-051', '89.00');
  await expect(page.getByTestId('notice')).toHaveText('Product 1051 created.');
  await expect(rows(page)).toHaveCount(3);
  await expect(page.locator('tr[data-product-id="1051"]')).toContainText('Counter Scanner');
  await app.close();
});

test('an ADMIN edits and deactivates a product; there is no delete', async () => {
  const { app, page } = await launch('ADMIN');
  await page.getByTestId('view-1001').click();
  await expect(page.getByTestId('product-detail').getByText('Delete')).toHaveCount(0);

  await page.fill('[data-testid=product-edit] #c-list_price', '119.90');
  await page.getByText('Save changes').click();
  await expect(page.getByTestId('notice')).toHaveText('Product 1001 saved.');
  const patch = seen.filter((s) => s.method === 'PATCH').at(-1)!;
  expect(patch.body).toContain('<list_price>119.90</list_price>');
  expect(patch.body).not.toContain('<name>');
  await expect(page.locator('tr[data-product-id="1001"]')).toContainText('119.90');

  nextRefusal = { file: 'unknown-category', status: 422 };
  await page.selectOption('[data-testid=product-edit] #c-category_id', '11');
  await page.getByText('Save changes').click();
  await expect(page.getByTestId('error-code')).toHaveText('unknown_category');
  await expect(page.getByTestId('detail-category_id')).toHaveText('category_id: unknown');

  await page.getByTestId('deactivate').click();
  await page.getByTestId('confirm-deactivate').click();
  await expect(page.getByTestId('product-status')).toHaveText('Inactive');
  const off = seen.filter((s) => s.method === 'PATCH').at(-1)!;
  expect(off.body).toContain('<is_active>false</is_active>');
  expect(seen.some((s) => s.method === 'DELETE')).toBe(false);
  await app.close();
});

test('the category tree, its descendants, and an ADMIN creating and editing categories', async () => {
  const { app, page } = await launch('ADMIN');
  await page.getByRole('button', { name: 'Categories' }).click();
  await expect.poll(() => treeIds(page)).toEqual(['1', '11', '12']);

  await page.getByTestId('category-1').click();
  await expect(page.getByTestId('category-descendants').locator('li')).toHaveCount(3);
  await shot(page, '410-desktop-categories-04-tree-and-descendants');

  await page.getByTestId('new-category').click();
  await page.fill('#c-category_id', '31');
  await page.fill('#c-name', 'Aged cheese');
  await page.selectOption('[data-testid=category-new] #c-parent_category_id', '11');
  await page.getByText('Create category').click();
  await expect(page.getByTestId('notice')).toHaveText('Category 31 created.');
  await expect.poll(() => treeIds(page)).toEqual(['1', '11', '31', '12']);

  await page.getByTestId('category-31').click();
  await page.fill('[data-testid=category-edit] #c-name', 'Aged and hard cheese');
  await page.getByText('Save changes').click();
  await expect(page.getByTestId('category-31')).toHaveText('Aged and hard cheese (#31)');

  await page.getByTestId('category-12').click();
  await page.selectOption('[data-testid=category-edit] #c-parent_category_id', '');
  await page.getByText('Save changes').click();
  await expect(page.getByTestId('category-parent')).toHaveText('None (a root category)');
  expect(seen.filter((s) => s.method === 'PATCH').at(-1)!.body).toContain(
    '<parent_category_id xsi:nil="true"/>',
  );

  await page.getByTestId('delete').click();
  await page.getByTestId('confirm-delete').click();
  await expect(page.getByTestId('notice')).toHaveText('Category 12 deleted.');
  await expect.poll(() => treeIds(page)).toEqual(['1', '11', '31']);
  await app.close();
});

test('each refused category change is shown as returned, and the tree is unchanged after a reload', async () => {
  const { app, page } = await launch('ADMIN');
  await page.getByRole('button', { name: 'Categories' }).click();
  await expect.poll(() => treeIds(page)).toEqual(['1', '11', '12']);

  await page.getByTestId('category-1').click();
  nextRefusal = { file: 'category-cycle', status: 409 };
  await page.selectOption('[data-testid=category-edit] #c-parent_category_id', '11');
  let reads = treeReads;
  await page.getByText('Save changes').click();
  await expect(page.getByTestId('error-code')).toHaveText('category_cycle');
  await expect(page.getByTestId('detail-parent_category_id')).toHaveText('parent_category_id: cycle');
  await expect.poll(() => treeReads).toBeGreaterThan(reads);
  expect(await treeIds(page)).toEqual(['1', '11', '12']);

  for (const [file, code] of [
    ['category-has-products', 'category_has_products'],
    ['category-has-children', 'category_has_children'],
    ['category-has-customer-interest', 'category_has_customer_interest'],
  ]) {
    await page.getByTestId('category-11').click();
    nextRefusal = { file: file!, status: 409 };
    reads = treeReads;
    await page.getByTestId('delete').click();
    await page.getByTestId('confirm-delete').click();
    await expect(page.getByTestId('error-code')).toHaveText(code!);
    await expect.poll(() => treeReads).toBeGreaterThan(reads);
    expect(await treeIds(page)).toEqual(['1', '11', '12']);
  }
  await app.close();
});

test('read-only roles see no write control, and a forced write is refused with forbidden_role', async () => {
  const { app, page } = await launch('ANALYST');
  await expect(rows(page)).toHaveCount(2);
  await expect(page.getByTestId('new-product')).toHaveCount(0);
  await page.getByTestId('view-1001').click();
  await expect(page.getByTestId('product-sku')).toHaveText('MOS-CAM-001');
  await expect(page.getByTestId('product-edit')).toHaveCount(0);
  await expect(page.getByTestId('deactivate')).toHaveCount(0);

  await page.getByRole('button', { name: 'Categories' }).click();
  await page.getByTestId('category-11').click();
  await expect(page.getByTestId('category-name')).toHaveText('Cheese');
  await expect(page.getByTestId('new-category')).toHaveCount(0);
  await expect(page.getByTestId('category-edit')).toHaveCount(0);
  await expect(page.getByTestId('delete')).toHaveCount(0);

  const forced = await page.evaluate(() => window.mosaiq.deactivateProduct(1001));
  expect(forced).toMatchObject({
    ok: false,
    error: { code: 'forbidden_role', message: 'The authenticated role cannot perform this operation.' },
  });
  await app.close();
});
