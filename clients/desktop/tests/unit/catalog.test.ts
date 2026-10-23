import { describe, expect, it } from 'vitest';
import { Catalog, treeOf } from '../../src/main/catalog';
import { errorXml, example, makeOffice, MemoryVault, type Responder, type Seen } from '../support/fake';

const PRODUCTS_NS = 'https://mosaiq.maxthecoder.online/xml/products/v1';
const CATEGORIES_NS = 'https://mosaiq.maxthecoder.online/xml/categories/v1';

const category = (id: number, name: string, parent?: number): string =>
  `<Category><category_id>${id}</category_id><name>${name}</name>${
    parent === undefined ? '' : `<parent_category_id>${parent}</parent_category_id>`
  }</Category>`;

const categoryPage = (items: string, page: number, size: number, total: number): string =>
  `<Categories xmlns="${CATEGORIES_NS}" xmlns:common="https://mosaiq.maxthecoder.online/xml/common/v1">${items}<common:PageInfo><common:page>${page}</common:page><common:size>${size}</common:size><common:total>${total}</common:total></common:PageInfo></Categories>`;

function catalog(responder: Responder): { catalog: Catalog; seen: Seen[] } {
  const vault = new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') });
  const { office, seen } = makeOffice(responder, vault);
  return { catalog: new Catalog(office), seen };
}

/** Answers like the two services, from the recorded examples. */
const services: Responder = (r) => {
  const path = new URL(r.url).pathname;
  if (path === '/api/products/v1/products' && r.method === 'GET')
    return { status: 200, body: example('products', 'products') };
  if (path === '/api/products/v1/products/1001' && r.method === 'GET')
    return { status: 200, body: example('products', 'product') };
  if (path === '/api/products/v1/products' && r.method === 'POST')
    return { status: 201, body: example('products', 'create-product') };
  if (path.startsWith('/api/products/v1/products/') && r.method === 'PATCH')
    return { status: 200, body: example('products', 'update-product') };
  if (path === '/api/categories/v1/categories/12')
    return {
      status: 200,
      body: example('categories', 'get-category').replace(/11|Cheese/g, (m) =>
        m === '11' ? '12' : 'Yogurt',
      ),
    };
  return { status: 404, body: errorXml('not_found') };
};

describe('products', () => {
  it('sends the filters and paging as a query, and names categories from categories-service', async () => {
    const { catalog: c, seen } = catalog(services);
    const res = await c.listProducts({
      q: 'cam era',
      sku: 'MOS-CAM-001',
      category_id: 12,
      active: true,
      page: 2,
      size: 50,
    });

    expect(res.ok).toBe(true);
    const list = seen.find((s) => s.url.includes('/api/products/v1/products?'))!;
    const query = new URL(list.url).searchParams;
    expect(Object.fromEntries(query)).toEqual({
      q: 'cam era',
      sku: 'MOS-CAM-001',
      category_id: '12',
      active: 'true',
      page: '2',
      size: '50',
    });
    if (!res.ok) return;
    expect(res.value.items.map((p) => [p.product_id, p.category_name, p.is_active])).toEqual([
      [1001, 'Yogurt', true],
      [1002, 'Yogurt', false],
    ]);
    expect(res.value.page).toEqual({ page: 1, size: 20, total: 2 });
    // Both products share category 12: it is read once, from categories-service.
    expect(seen.filter((s) => s.url.endsWith('/api/categories/v1/categories/12'))).toHaveLength(1);
  });

  it('shows a category that cannot be read as unavailable instead of failing the list', async () => {
    const { catalog: c } = catalog((r) =>
      r.url.includes('/api/categories/')
        ? { status: 404, body: example('categories', 'category-not-found') }
        : services(r),
    );
    const res = await c.listProducts({});
    expect(res.ok && res.value.items[0]?.category_name).toBe('Category 12 (unavailable)');
  });

  it.each([
    [{ size: 0 }, 'The page size'],
    [{ size: 101 }, 'The page size'],
    [{ page: 1.5 }, 'The page'],
    [{ category_id: 40000 }, 'The category'],
    [{ active: 'yes' }, 'Active'],
  ])('refuses %o before anything is sent', async (query, label) => {
    const { catalog: c, seen } = catalog(services);
    const res = await c.listProducts(query);
    expect(!res.ok && res.error.code).toBe('invalid_input');
    expect(!res.ok && res.error.message).toContain(label);
    expect(seen).toHaveLength(0);
  });

  it('creates a product with the contract body', async () => {
    const { catalog: c, seen } = catalog(services);
    const res = await c.createProduct({
      product_id: 1051,
      sku: 'MOS-SCN-051',
      name: 'Counter Scanner',
      category_id: 12,
      list_price: '89.00',
    });
    expect(res.ok).toBe(true);
    const post = seen.find((s) => s.method === 'POST')!;
    expect(post.body).toBe(
      `<?xml version="1.0" encoding="UTF-8"?><CreateProductRequest xmlns="${PRODUCTS_NS}"><product_id>1051</product_id><sku>MOS-SCN-051</sku><name>Counter Scanner</name><category_id>12</category_id><list_price>89.00</list_price></CreateProductRequest>`,
    );
  });

  it('refuses a negative price next to its field, and sends nothing', async () => {
    const { catalog: c, seen } = catalog(services);
    const res = await c.createProduct({
      product_id: 1,
      sku: 'S',
      name: 'N',
      category_id: 12,
      list_price: '-1.00',
    });
    expect(!res.ok && res.error.code).toBe('invalid_body');
    expect(!res.ok && res.error.details).toEqual([{ field: 'list_price', code: 'invalid_format' }]);
    expect(seen).toHaveLength(0);
  });

  it('sends only the changed fields, in the order the XSD fixes', async () => {
    const { catalog: c, seen } = catalog(services);
    await c.updateProduct(1002, { is_active: false, list_price: '22.00', name: 'Shelf Tag Pack - Retired' });
    const patch = seen.find((s) => s.method === 'PATCH')!;
    expect(patch.url).toMatch(/\/api\/products\/v1\/products\/1002$/);
    expect(patch.body).toContain(
      '<name>Shelf Tag Pack - Retired</name><list_price>22.00</list_price><is_active>false</is_active>',
    );
    expect(patch.body).not.toContain('category_id');
  });

  it('deactivates by a PATCH of is_active to false, never a DELETE', async () => {
    const { catalog: c, seen } = catalog(services);
    await c.deactivateProduct(1002);
    expect(seen.map((s) => s.method)).not.toContain('DELETE');
    const patch = seen.find((s) => s.method === 'PATCH')!;
    expect(patch.body).toContain('<UpdateProductRequest');
    expect(patch.body).toContain('<is_active>false</is_active>');
  });

  it.each([
    ['sku-taken', 409, 'sku_taken'],
    ['unknown-category', 422, 'unknown_category'],
    ['product-not-found', 404, 'product_not_found'],
  ])('shows the %s refusal as returned', async (file, status, code) => {
    const { catalog: c } = catalog(() => ({ status, body: example('products', file) }));
    const res = await c.updateProduct(1001, { category_id: 99 });
    expect(!res.ok && res.error.code).toBe(code);
    expect(!res.ok && res.error.correlation_id).toMatch(/^corr-/);
  });

  it('keeps the field details of a refusal', async () => {
    const { catalog: c } = catalog(() => ({ status: 422, body: example('products', 'unknown-category') }));
    const res = await c.updateProduct(1001, { category_id: 99 });
    expect(!res.ok && res.error.details).toEqual([{ field: 'category_id', code: 'unknown' }]);
  });

  it('shows forbidden_role as returned', async () => {
    const { catalog: c } = catalog(() => ({ status: 403, body: example('categories', 'forbidden-role') }));
    const res = await c.deactivateProduct(1001);
    expect(!res.ok && res.error.code).toBe('forbidden_role');
  });
});

describe('categories', () => {
  it('builds the tree from every page of the list', async () => {
    const { catalog: c, seen } = catalog((r) => {
      const page = new URL(r.url).searchParams.get('page');
      return page === '1'
        ? { status: 200, body: categoryPage(category(1, 'Dairy') + category(11, 'Cheese', 1), 1, 2, 3) }
        : { status: 200, body: categoryPage(category(31, 'Aged cheese', 11), 2, 2, 3) };
    });
    const res = await c.categoryTree();
    expect(seen.map((s) => new URL(s.url).searchParams.get('page'))).toEqual(['1', '2']);
    expect(res.ok && res.value).toEqual([
      {
        category_id: 1,
        name: 'Dairy',
        parent_category_id: null,
        children: [
          {
            category_id: 11,
            name: 'Cheese',
            parent_category_id: 1,
            children: [{ category_id: 31, name: 'Aged cheese', parent_category_id: 11, children: [] }],
          },
        ],
      },
    ]);
  });

  it("lists a category's direct children and its descendants", async () => {
    const { catalog: c, seen } = catalog(() => ({
      status: 200,
      body: example('categories', 'list-descendants'),
    }));
    await c.listCategories(1, 1);
    const res = await c.categoryDescendants(1, 2);
    expect(new URL(seen[0]!.url).searchParams.get('parent_id')).toBe('1');
    expect(new URL(seen[1]!.url).pathname).toBe('/api/categories/v1/categories/1/descendants');
    expect(new URL(seen[1]!.url).searchParams.get('page')).toBe('2');
    expect(res.ok && res.value.items.map((x) => x.category_id)).toEqual([11, 12, 31]);
  });

  it('makes a category a root with xsi:nil', async () => {
    const { catalog: c, seen } = catalog(() => ({
      status: 200,
      body: example('categories', 'get-root-category'),
    }));
    const res = await c.updateCategory(11, { parent_category_id: null });
    expect(res.ok).toBe(true);
    expect(seen[0]!.method).toBe('PATCH');
    expect(seen[0]!.body).toContain('<parent_category_id xsi:nil="true"/>');
  });

  it('deletes with a DELETE and no body', async () => {
    const { catalog: c, seen } = catalog(() => ({ status: 204 }));
    const res = await c.deleteCategory(31);
    expect(res).toEqual({ ok: true, value: null });
    expect(seen[0]).toMatchObject({ method: 'DELETE', body: undefined });
    expect(seen[0]!.url).toMatch(/\/api\/categories\/v1\/categories\/31$/);
  });

  it.each([
    ['category-cycle', 'category_cycle'],
    ['category-has-products', 'category_has_products'],
    ['category-has-children', 'category_has_children'],
    ['category-has-customer-interest', 'category_has_customer_interest'],
  ])('shows the %s refusal as returned', async (file, code) => {
    const { catalog: c } = catalog(() => ({ status: 409, body: example('categories', file) }));
    const res =
      file === 'category-cycle'
        ? await c.updateCategory(1, { parent_category_id: 31 })
        : await c.deleteCategory(1);
    expect(!res.ok && res.error.code).toBe(code);
  });

  it('refuses a name longer than the contract allows', async () => {
    const { catalog: c, seen } = catalog(() => ({ status: 500 }));
    const res = await c.createCategory({ category_id: 40, name: 'x'.repeat(81), parent_category_id: null });
    expect(!res.ok && res.error.details).toEqual([{ field: 'name', code: 'too_long' }]);
    expect(seen).toHaveLength(0);
  });

  it('puts a category whose parent is missing at the root', () => {
    expect(
      treeOf([{ category_id: 5, name: 'Orphan', parent_category_id: 99 }]).map((n) => n.category_id),
    ).toEqual([5]);
  });
});
