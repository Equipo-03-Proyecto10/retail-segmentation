import type {
  CategoryNode,
  CategoryView,
  MosaiqBridge,
  ProductChanges,
  ProductQuery,
  ProductView,
} from '../shared/api';
import {
  button,
  el,
  errorBox,
  formField,
  markDetails,
  pager,
  select,
  show,
  testid,
  textInput,
  type Details,
} from './dom';
import { latestRequest } from './latest-request';

// Products and the category tree (F16-02, #410). Every call goes through a
// named bridge operation; a write control is drawn only when the signed-in
// role may call its operation, and a refusal is shown as the service returned it.

export interface ScreenContext {
  api: MosaiqBridge;
  /** Whether the signed-in role may call this operationId. */
  can(operationId: string): boolean;
}

/** The tree as indented options, for choosing a category or a parent. */
function categoryOptions(tree: CategoryNode[]): [string, string][] {
  const out: [string, string][] = [];
  const walk = (nodes: CategoryNode[], depth: number): void => {
    for (const n of nodes) {
      out.push([String(n.category_id), `${'— '.repeat(depth)}${n.name} (#${n.category_id})`]);
      walk(n.children, depth + 1);
    }
  };
  walk(tree, 0);
  return out;
}

// ---------- products ----------

const PRODUCT_FIELDS = ['product_id', 'sku', 'name', 'category_id', 'list_price', 'is_active'];

export function renderProducts(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const message = testid(el('div'), 'products-message');
  const tableArea = el('div');
  const detail = testid(el('section', { cls: 'panel' }), 'product-detail');
  const renderLatestList = latestRequest();
  const renderLatestDetail = latestRequest();
  let tree: CategoryNode[] = [];
  let query: ProductQuery = { page: 1, size: 20 };

  const q = textInput('', 'search');
  const sku = textInput('');
  const category = select([['', 'Any category']], '');
  const active = select(
    [
      ['', 'Active or not'],
      ['true', 'Active'],
      ['false', 'Inactive'],
    ],
    '',
  );
  const size = select(
    [
      ['20', '20 a page'],
      ['50', '50 a page'],
      ['100', '100 a page'],
    ],
    '20',
  );
  const search = el('button', { text: 'Search' });
  search.type = 'submit';
  const filters = testid(
    el(
      'form',
      { cls: 'filters' },
      formField('q', 'Name or SKU contains', q, [], 'filter'),
      formField('sku', 'Exact SKU', sku, [], 'filter'),
      formField('category_id', 'Category', category, [], 'filter'),
      formField('active', 'Status', active, [], 'filter'),
      formField('size', 'Page size', size, [], 'filter'),
      search,
    ),
    'product-filters',
  );
  filters.noValidate = true;
  filters.addEventListener('submit', (event) => {
    event.preventDefault();
    query = { page: 1, size: Number(size.value) };
    if (q.value.trim()) query.q = q.value.trim();
    if (sku.value.trim()) query.sku = sku.value.trim();
    if (category.value) query.category_id = Number(category.value);
    if (active.value) query.active = active.value === 'true';
    void load();
  });

  async function load(): Promise<void> {
    await renderLatestList(
      () => api.listProducts(query),
      (res) => {
        if (!res.ok) return void tableArea.replaceChildren(errorBox(res.error));
        const body = el('tbody');
        for (const p of res.value.items) {
          const view = button('View', `view-${p.product_id}`, () => void open(p.product_id));
          const row = el(
            'tr',
            {},
            el('td', { text: String(p.product_id) }),
            el('td', { text: p.sku }),
            el('td', { text: p.name }),
            el('td', { text: p.category_name }),
            el('td', { text: p.list_price, cls: 'num' }),
            el('td', { text: p.is_active ? 'Active' : 'Inactive' }),
            el('td', {}, view),
          );
          row.dataset.productId = String(p.product_id);
          body.append(row);
        }
        const head = el('tr');
        for (const h of ['ID', 'SKU', 'Name', 'Category', 'List price', 'Status', ''])
          head.append(el('th', { text: h }));
        tableArea.replaceChildren(
          testid(el('table', {}, el('thead', {}, head), body), 'products-table'),
          pager(res.value.page, 'products', (page) => {
            query = { ...query, page };
            void load();
          }),
        );
      },
    );
  }

  async function open(productId: number): Promise<void> {
    await renderLatestDetail(
      () => api.getProduct(productId),
      (res) => {
        if (!res.ok) return void detail.replaceChildren(errorBox(res.error));
        showProduct(res.value);
      },
    );
  }

  function showProduct(p: ProductView): void {
    const facts = el('dl');
    const fact = (name: string, value: string, id: string): void => {
      facts.append(el('dt', { text: name }), testid(el('dd', { text: value }), id));
    };
    fact('ID', String(p.product_id), 'product-id');
    fact('SKU', p.sku, 'product-sku');
    fact('Name', p.name, 'product-name');
    fact('Category', p.category_name, 'product-category');
    fact('List price', p.list_price, 'product-price');
    fact('Status', p.is_active ? 'Active' : 'Inactive', 'product-status');
    const parts: Node[] = [el('h3', { text: `Product ${p.product_id}` }), facts];
    if (ctx.can('updateProduct')) parts.push(editForm(p), deactivateControl(p));
    // Products are retired, never deleted (RN-11): there is no delete control.
    detail.replaceChildren(...parts);
  }

  function editForm(p: ProductView): HTMLElement {
    const name = textInput(p.name);
    const cat = select(categoryOptions(tree), String(p.category_id));
    if (cat.value !== String(p.category_id)) {
      cat.append(Object.assign(el('option', { text: p.category_name }), { value: String(p.category_id) }));
      cat.value = String(p.category_id);
    }
    const price = textInput(p.list_price);
    const save = el('button', { text: 'Save changes' });
    save.type = 'submit';
    const form = testid(
      el(
        'form',
        {},
        el('h4', { text: 'Edit' }),
        formField('name', 'Name', name, []),
        formField('category_id', 'Category', cat, []),
        formField('list_price', 'List price', price, []),
        save,
      ),
      'product-edit',
    );
    form.noValidate = true;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const changes: ProductChanges = {};
      if (name.value !== p.name) changes.name = name.value;
      if (Number(cat.value) !== p.category_id) changes.category_id = Number(cat.value);
      if (price.value !== p.list_price) changes.list_price = price.value;
      const isCurrent = renderLatestDetail.begin();
      void api.updateProduct(p.product_id, changes).then(async (res) => {
        if (!isCurrent()) return;
        show(message, res, `Product ${p.product_id} saved.`, PRODUCT_FIELDS);
        if (res.ok) showProduct(res.value);
        else markDetails(form, res.error.details);
        await load();
      });
    });
    return form;
  }

  function deactivateControl(p: ProductView): HTMLElement {
    const area = el('div');
    if (!p.is_active) return area;
    const confirm = button('Confirm deactivation', 'confirm-deactivate', () => {
      const isCurrent = renderLatestDetail.begin();
      void api.deactivateProduct(p.product_id).then(async (res) => {
        if (!isCurrent()) return;
        show(message, res, `Product ${p.product_id} deactivated.`);
        if (res.ok) showProduct(res.value);
        await load();
      });
    });
    area.append(
      button('Deactivate', 'deactivate', () => {
        area.replaceChildren(
          el('p', { text: 'The product stays in the catalog and in past sales, marked inactive.' }),
          confirm,
        );
      }),
    );
    return area;
  }

  function newProductForm(values: Record<string, string> = {}, details: Details = []): HTMLElement {
    const id = textInput(values.product_id ?? '', 'number');
    const s = textInput(values.sku ?? '');
    const n = textInput(values.name ?? '');
    const c = select([['', 'Choose a category'], ...categoryOptions(tree)], values.category_id ?? '');
    const price = textInput(values.list_price ?? '');
    const create = el('button', { text: 'Create product' });
    create.type = 'submit';
    const form = testid(
      el(
        'form',
        {},
        el('h3', { text: 'New product' }),
        formField('product_id', 'Product ID', id, details),
        formField('sku', 'SKU', s, details),
        formField('name', 'Name', n, details),
        formField('category_id', 'Category', c, details),
        formField('list_price', 'List price', price, details),
        create,
      ),
      'product-new',
    );
    form.noValidate = true;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const typed = {
        product_id: id.value,
        sku: s.value,
        name: n.value,
        category_id: c.value,
        list_price: price.value,
      };
      const isCurrent = renderLatestDetail.begin();
      void api
        .createProduct({
          product_id: Number(id.value),
          sku: s.value,
          name: n.value,
          category_id: Number(c.value),
          list_price: price.value,
        })
        .then(async (res) => {
          if (!isCurrent()) return;
          show(message, res, `Product ${id.value} created.`, PRODUCT_FIELDS);
          if (res.ok) showProduct(res.value);
          else detail.replaceChildren(newProductForm(typed, res.error.details));
          await load();
        });
    });
    return form;
  }

  const header: Node[] = [el('h2', { text: 'Products' })];
  if (ctx.can('createProduct'))
    header.push(
      button('New product', 'new-product', () => {
        renderLatestDetail.begin();
        message.replaceChildren();
        detail.replaceChildren(newProductForm());
      }),
    );
  host.replaceChildren(...header, filters, message, tableArea, detail);

  void api.categoryTree().then((res) => {
    if (res.ok) {
      tree = res.value;
      for (const [v, text] of categoryOptions(tree))
        category.append(Object.assign(el('option', { text }), { value: v }));
    } else {
      message.replaceChildren(errorBox(res.error));
    }
  });
  void load();
}

// ---------- categories ----------

const CATEGORY_FIELDS = ['category_id', 'name', 'parent_category_id'];

export function renderCategories(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const message = testid(el('div'), 'categories-message');
  const treeArea = el('div');
  const detail = testid(el('section', { cls: 'panel' }), 'category-detail');
  const renderLatestTree = latestRequest();
  const renderLatestDescendants = latestRequest();
  const renderLatestDetail = latestRequest();
  let tree: CategoryNode[] = [];

  function find(id: number, nodes: CategoryNode[] = tree): CategoryNode | undefined {
    for (const n of nodes) {
      if (n.category_id === id) return n;
      const hit = find(id, n.children);
      if (hit) return hit;
    }
    return undefined;
  }

  async function loadTree(): Promise<void> {
    await renderLatestTree(
      () => api.categoryTree(),
      (res) => {
        if (!res.ok) return void treeArea.replaceChildren(errorBox(res.error));
        tree = res.value;
        const list = (nodes: CategoryNode[]): HTMLElement => {
          const ul = el('ul', { cls: 'tree' });
          for (const n of nodes) {
            const pick = button(`${n.name} (#${n.category_id})`, `category-${n.category_id}`, () =>
              showCategory(n),
            );
            const li = el('li', {}, pick);
            li.dataset.categoryId = String(n.category_id);
            if (n.children.length > 0) li.append(list(n.children));
            ul.append(li);
          }
          return ul;
        };
        treeArea.replaceChildren(testid(list(tree), 'category-tree'));
      },
    );
  }

  function parentOptions(excluding?: number): [string, string][] {
    return [
      ['', 'No parent (a root category)'],
      ...categoryOptions(tree).filter(([v]) => v !== String(excluding)),
    ];
  }

  function showCategory(c: CategoryView): void {
    renderLatestDetail.begin();
    const parent = c.parent_category_id === null ? undefined : find(c.parent_category_id);
    const facts = el('dl');
    const fact = (name: string, value: string, id: string): void => {
      facts.append(el('dt', { text: name }), testid(el('dd', { text: value }), id));
    };
    fact('ID', String(c.category_id), 'category-id');
    fact('Name', c.name, 'category-name');
    fact(
      'Parent',
      c.parent_category_id === null
        ? 'None (a root category)'
        : `${parent?.name ?? 'Category'} (#${c.parent_category_id})`,
      'category-parent',
    );
    const below = testid(el('div'), 'category-descendants');
    const parts: Node[] = [
      el('h3', { text: c.name }),
      facts,
      el('h4', { text: 'Every category below' }),
      below,
    ];
    if (ctx.can('updateCategory')) parts.push(editForm(c));
    if (ctx.can('deleteCategory')) parts.push(deleteControl(c));
    detail.replaceChildren(...parts);
    void descendants(c.category_id, 1, below);
  }

  async function descendants(id: number, page: number, area: HTMLElement): Promise<void> {
    await renderLatestDescendants(
      () => api.categoryDescendants(id, page),
      (res) => {
        if (!res.ok) return void area.replaceChildren(errorBox(res.error));
        const ul = el('ul');
        for (const d of res.value.items) ul.append(el('li', { text: `${d.name} (#${d.category_id})` }));
        area.replaceChildren(
          res.value.items.length === 0 ? el('p', { text: 'No category is below this one.' }) : ul,
          pager(res.value.page, 'descendants', (p) => void descendants(id, p, area)),
        );
      },
    );
  }

  function editForm(c: CategoryView): HTMLElement {
    const name = textInput(c.name);
    const parent = select(
      parentOptions(c.category_id),
      c.parent_category_id === null ? '' : String(c.parent_category_id),
    );
    const save = el('button', { text: 'Save changes' });
    save.type = 'submit';
    const form = testid(
      el(
        'form',
        {},
        el('h4', { text: 'Edit' }),
        formField('name', 'Name', name, []),
        formField('parent_category_id', 'Parent', parent, []),
        save,
      ),
      'category-edit',
    );
    form.noValidate = true;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const changes: { name?: string; parent_category_id?: number | null } = {};
      if (name.value !== c.name) changes.name = name.value;
      const chosen = parent.value === '' ? null : Number(parent.value);
      if (chosen !== c.parent_category_id) changes.parent_category_id = chosen;
      const isCurrent = renderLatestDetail.begin();
      void api.updateCategory(c.category_id, changes).then(async (res) => {
        if (!isCurrent()) return;
        show(message, res, `Category ${c.category_id} saved.`, CATEGORY_FIELDS);
        // Reloaded either way: after a refusal the tree is shown as the service still has it.
        await loadTree();
        if (!isCurrent()) return;
        if (res.ok) showCategory(res.value);
        else markDetails(form, res.error.details);
      });
    });
    return form;
  }

  function deleteControl(c: CategoryView): HTMLElement {
    const area = el('div');
    const confirm = button('Confirm delete', 'confirm-delete', () => {
      const isCurrent = renderLatestDetail.begin();
      void api.deleteCategory(c.category_id).then(async (res) => {
        if (!isCurrent()) return;
        show(message, res, `Category ${c.category_id} deleted.`);
        await loadTree();
        if (!isCurrent()) return;
        if (res.ok) detail.replaceChildren();
        else showCategory(c);
      });
    });
    area.append(
      button('Delete', 'delete', () => {
        area.replaceChildren(
          el('p', {
            text: 'Deleting cannot be undone. A category with products, children or customer interest is refused.',
          }),
          confirm,
        );
      }),
    );
    return area;
  }

  function newCategoryForm(values: Record<string, string> = {}, details: Details = []): HTMLElement {
    const id = textInput(values.category_id ?? '', 'number');
    const name = textInput(values.name ?? '');
    const parent = select(parentOptions(), values.parent_category_id ?? '');
    const create = el('button', { text: 'Create category' });
    create.type = 'submit';
    const form = testid(
      el(
        'form',
        {},
        el('h3', { text: 'New category' }),
        formField('category_id', 'Category ID', id, details),
        formField('name', 'Name', name, details),
        formField('parent_category_id', 'Parent', parent, details),
        create,
      ),
      'category-new',
    );
    form.noValidate = true;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const typed = { category_id: id.value, name: name.value, parent_category_id: parent.value };
      const isCurrent = renderLatestDetail.begin();
      void api
        .createCategory({
          category_id: Number(id.value),
          name: name.value,
          parent_category_id: parent.value === '' ? null : Number(parent.value),
        })
        .then(async (res) => {
          if (!isCurrent()) return;
          show(message, res, `Category ${id.value} created.`, CATEGORY_FIELDS);
          await loadTree();
          if (!isCurrent()) return;
          if (res.ok) showCategory(res.value);
          else detail.replaceChildren(newCategoryForm(typed, res.error.details));
        });
    });
    return form;
  }

  const header: Node[] = [el('h2', { text: 'Categories' })];
  if (ctx.can('createCategory'))
    header.push(
      button('New category', 'new-category', () => {
        renderLatestDetail.begin();
        renderLatestDescendants.begin();
        message.replaceChildren();
        detail.replaceChildren(newCategoryForm());
      }),
    );
  host.replaceChildren(...header, message, treeArea, detail);
  void loadTree();
}
