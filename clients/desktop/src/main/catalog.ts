import type {
  CategoryChanges,
  CategoryDraft,
  CategoryNode,
  CategoryView,
  Paged,
  ProductChanges,
  ProductDraft,
  ProductQuery,
  ProductView,
  Result,
} from '../shared/api';
import type { Backoffice } from './backoffice';
import {
  checked,
  failed,
  FieldChecks,
  id,
  invalidInput,
  MAX_INT,
  optionalText,
  pageNumber,
  pageOf,
  pageSize,
  record,
} from './checks';
import type { CallResult } from './transport';
import type { FieldValue } from './xml';

// Products and the category tree (F16-02, #410). Every value from the screens is
// checked here, in the main process, before a path or a body is built from it.

const MAX_PRODUCT_ID = MAX_INT;
const MAX_CATEGORY_ID = 32767;
const TREE_PAGE_SIZE = 100;
/** A tree of 32,767 categories at 100 a page; a service that never ends its pages is cut off here. */
const TREE_PAGE_LIMIT = 328;

interface ProductXml {
  product_id: string;
  sku: string;
  name: string;
  category_id: string;
  list_price: string;
  is_active: string;
  image_path?: string;
}

interface CategoryXml {
  category_id: string;
  name: string;
  parent_category_id?: string;
}

function categoryOf(x: CategoryXml): CategoryView {
  return {
    category_id: Number(x.category_id),
    name: x.name,
    parent_category_id: x.parent_category_id === undefined ? null : Number(x.parent_category_id),
  };
}

export class Catalog {
  constructor(private readonly office: Backoffice) {}

  private run<T>(work: () => Promise<Result<T>>): Promise<Result<T>> {
    return checked(this.office, work);
  }

  // ---------- products ----------

  listProducts(query: unknown): Promise<Result<Paged<ProductView>>> {
    return this.run(async () => {
      const q = record(query ?? {}, 'The search') as ProductQuery;
      const params = new URLSearchParams();
      const text = optionalText(q.q, 'The search text');
      if (text) params.set('q', text);
      const sku = optionalText(q.sku, 'The SKU');
      if (sku) params.set('sku', sku);
      if (q.category_id !== undefined)
        params.set('category_id', String(id(q.category_id, MAX_CATEGORY_ID, 'The category')));
      if (q.active !== undefined) {
        if (typeof q.active !== 'boolean') throw invalidInput('Active must be yes or no.');
        params.set('active', String(q.active));
      }
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.office.authed({ method: 'GET', path: `/v1/products?${params.toString()}` });
      if (!res.ok) return failed(res);
      const page = res.data?.Products as { Product?: ProductXml[] } & Record<string, unknown>;
      const names = new Map<number, Promise<string>>();
      const items = await Promise.all((page.Product ?? []).map((p) => this.productOf(p, names)));
      return { ok: true, value: { items, page: pageOf(page) } };
    });
  }

  getProduct(productId: unknown): Promise<Result<ProductView>> {
    return this.run(async () => {
      const pid = id(productId, MAX_PRODUCT_ID, 'The product');
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/products/${encodeURIComponent(pid)}`,
      });
      return this.productResult(res);
    });
  }

  createProduct(draft: unknown): Promise<Result<ProductView>> {
    return this.run(async () => {
      const d = record(draft, 'The product') as Partial<Record<keyof ProductDraft, unknown>>;
      const f = new FieldChecks();
      const fields: Record<string, FieldValue> = {
        product_id: String(f.id('product_id', d.product_id, MAX_PRODUCT_ID)),
        sku: f.text('sku', d.sku, 40),
        name: f.text('name', d.name, 150),
        category_id: String(f.id('category_id', d.category_id, MAX_CATEGORY_ID)),
        list_price: f.price('list_price', d.list_price),
      };
      f.done();
      const res = await this.office.authed({
        method: 'POST',
        path: '/v1/products',
        body: { root: 'CreateProductRequest', fields },
      });
      return this.productResult(res);
    });
  }

  updateProduct(productId: unknown, changes: unknown): Promise<Result<ProductView>> {
    return this.run(async () => {
      const pid = id(productId, MAX_PRODUCT_ID, 'The product');
      const c = record(changes, 'The changes') as Partial<Record<keyof ProductChanges, unknown>>;
      const f = new FieldChecks();
      // The XSD fixes this order: name, category_id, list_price, is_active.
      const fields: Record<string, FieldValue> = {};
      if (c.name !== undefined) fields.name = f.text('name', c.name, 150);
      if (c.category_id !== undefined)
        fields.category_id = String(f.id('category_id', c.category_id, MAX_CATEGORY_ID));
      if (c.list_price !== undefined) fields.list_price = f.price('list_price', c.list_price);
      if (c.is_active !== undefined) {
        if (typeof c.is_active !== 'boolean') f.details.push({ field: 'is_active', code: 'invalid_format' });
        fields.is_active = String(c.is_active);
      }
      f.done();
      if (Object.keys(fields).length === 0) throw invalidInput('Nothing was changed.');
      return this.patchProduct(pid, fields);
    });
  }

  /** Products are retired, never deleted (RN-11). */
  deactivateProduct(productId: unknown): Promise<Result<ProductView>> {
    return this.run(async () =>
      this.patchProduct(id(productId, MAX_PRODUCT_ID, 'The product'), { is_active: 'false' }),
    );
  }

  private async patchProduct(pid: number, fields: Record<string, FieldValue>): Promise<Result<ProductView>> {
    const res = await this.office.authed({
      method: 'PATCH',
      path: `/v1/products/${encodeURIComponent(pid)}`,
      body: { root: 'UpdateProductRequest', fields },
    });
    return this.productResult(res);
  }

  private async productResult(res: CallResult): Promise<Result<ProductView>> {
    if (!res.ok) return failed(res);
    return { ok: true, value: await this.productOf(res.data?.Product as ProductXml, new Map()) };
  }

  /** A product with its category's name, read from categories-service once per category. */
  private async productOf(p: ProductXml, names: Map<number, Promise<string>>): Promise<ProductView> {
    const categoryId = Number(p.category_id);
    let name = names.get(categoryId);
    if (name === undefined) {
      name = this.categoryName(categoryId);
      names.set(categoryId, name);
    }
    const view: ProductView = {
      product_id: Number(p.product_id),
      sku: p.sku,
      name: p.name,
      category_id: categoryId,
      category_name: await name,
      list_price: p.list_price,
      is_active: p.is_active === 'true' || p.is_active === '1',
    };
    if (p.image_path !== undefined) view.image_path = p.image_path;
    return view;
  }

  private async categoryName(categoryId: number): Promise<string> {
    const res = await this.office.authed({
      method: 'GET',
      path: `/v1/categories/${encodeURIComponent(categoryId)}`,
    });
    if (!res.ok) return `Category ${categoryId} (unavailable)`;
    return (res.data?.Category as CategoryXml).name;
  }

  // ---------- categories ----------

  categoryTree(): Promise<Result<CategoryNode[]>> {
    return this.run(async () => {
      const all: CategoryView[] = [];
      for (let page = 1; page <= TREE_PAGE_LIMIT; page += 1) {
        const res = await this.categoryPage('/v1/categories', new URLSearchParams(), page, TREE_PAGE_SIZE);
        if (!res.ok) return res;
        all.push(...res.value.items);
        if (res.value.items.length === 0 || all.length >= res.value.page.total) break;
      }
      return { ok: true, value: treeOf(all) };
    });
  }

  listCategories(parentId: unknown, page: unknown): Promise<Result<Paged<CategoryView>>> {
    return this.run(async () => {
      const params = new URLSearchParams();
      if (parentId !== null && parentId !== undefined)
        params.set('parent_id', String(id(parentId, MAX_CATEGORY_ID, 'The parent category')));
      return this.categoryPage('/v1/categories', params, pageNumber(page), TREE_PAGE_SIZE);
    });
  }

  categoryDescendants(categoryId: unknown, page: unknown): Promise<Result<Paged<CategoryView>>> {
    return this.run(async () => {
      const cid = id(categoryId, MAX_CATEGORY_ID, 'The category');
      return this.categoryPage(
        `/v1/categories/${encodeURIComponent(cid)}/descendants`,
        new URLSearchParams(),
        pageNumber(page),
        TREE_PAGE_SIZE,
      );
    });
  }

  private async categoryPage(
    path: string,
    params: URLSearchParams,
    page: number,
    size: number,
  ): Promise<Result<Paged<CategoryView>>> {
    params.set('page', String(page));
    params.set('size', String(size));
    const res = await this.office.authed({ method: 'GET', path: `${path}?${params.toString()}` });
    if (!res.ok) return failed(res);
    const data = res.data?.Categories as { Category?: CategoryXml[] } & Record<string, unknown>;
    return { ok: true, value: { items: (data.Category ?? []).map(categoryOf), page: pageOf(data) } };
  }

  createCategory(draft: unknown): Promise<Result<CategoryView>> {
    return this.run(async () => {
      const d = record(draft, 'The category') as Partial<Record<keyof CategoryDraft, unknown>>;
      const f = new FieldChecks();
      const fields: Record<string, FieldValue> = {
        category_id: String(f.id('category_id', d.category_id, MAX_CATEGORY_ID)),
        name: f.text('name', d.name, 80),
      };
      if (d.parent_category_id !== null && d.parent_category_id !== undefined)
        fields.parent_category_id = String(f.id('parent_category_id', d.parent_category_id, MAX_CATEGORY_ID));
      f.done();
      const res = await this.office.authed({
        method: 'POST',
        path: '/v1/categories',
        body: { root: 'CreateCategoryRequest', fields },
      });
      if (!res.ok) return failed(res);
      return { ok: true, value: categoryOf(res.data?.Category as CategoryXml) };
    });
  }

  updateCategory(categoryId: unknown, changes: unknown): Promise<Result<CategoryView>> {
    return this.run(async () => {
      const cid = id(categoryId, MAX_CATEGORY_ID, 'The category');
      const c = record(changes, 'The changes') as Partial<Record<keyof CategoryChanges, unknown>>;
      const f = new FieldChecks();
      const fields: Record<string, FieldValue> = {};
      if (c.name !== undefined) fields.name = f.text('name', c.name, 80);
      // null makes the category a root: sent as xsi:nil.
      if (c.parent_category_id === null) fields.parent_category_id = null;
      else if (c.parent_category_id !== undefined)
        fields.parent_category_id = String(f.id('parent_category_id', c.parent_category_id, MAX_CATEGORY_ID));
      f.done();
      if (Object.keys(fields).length === 0) throw invalidInput('Nothing was changed.');
      const res = await this.office.authed({
        method: 'PATCH',
        path: `/v1/categories/${encodeURIComponent(cid)}`,
        body: { root: 'UpdateCategoryRequest', fields },
      });
      if (!res.ok) return failed(res);
      return { ok: true, value: categoryOf(res.data?.Category as CategoryXml) };
    });
  }

  deleteCategory(categoryId: unknown): Promise<Result<null>> {
    return this.run(async () => {
      const cid = id(categoryId, MAX_CATEGORY_ID, 'The category');
      const res = await this.office.authed({
        method: 'DELETE',
        path: `/v1/categories/${encodeURIComponent(cid)}`,
      });
      if (!res.ok) return failed(res);
      return { ok: true, value: null };
    });
  }
}

/** Nests categories under their parents, each level ordered by id. A missing parent makes a root. */
export function treeOf(categories: CategoryView[]): CategoryNode[] {
  const nodes = new Map<number, CategoryNode>();
  for (const c of categories) nodes.set(c.category_id, { ...c, children: [] });
  const roots: CategoryNode[] = [];
  for (const node of nodes.values()) {
    const parent = node.parent_category_id === null ? undefined : nodes.get(node.parent_category_id);
    if (parent === undefined || parent === node) roots.push(node);
    else parent.children.push(node);
  }
  const sort = (list: CategoryNode[]): void => {
    list.sort((a, b) => a.category_id - b.category_id);
    for (const n of list) sort(n.children);
  };
  sort(roots);
  return roots;
}
