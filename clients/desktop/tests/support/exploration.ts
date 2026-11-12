import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// What the customer exploration tests (F28-03, #872) answer with. The documents of the contracts are read
// from disk; the few that have to vary (products, stores, categories, a profile that agrees with the sales)
// are built here in the shape of their recorded examples. Every response is validated against its XSD by
// the transport, so a document that drifts from its contract fails the test that uses it.

const CONTRACTS = join(__dirname, '..', '..', '..', '..', 'contracts');
const NS = 'https://mosaiq.maxthecoder.online/xml';
const COMMON = `${NS}/common/v1`;

export const CUSTOMER = '7b3ad781-c5de-4f40-ae50-1fa825ce2b25';
/** A customer the engine has not scored yet, and one with nothing to count. */
export const PENDING = '9c41e7b2-5d36-4f08-a1b9-3e2d7c6f5a40';
export const UNASSIGNED = '2f6c1d90-4a8e-4c3b-9d57-0b1e6a7c8d12';
/** A customer who withdrew personalization. */
export const OBJECTED = '2e6c9d14-83a0-4b7f-a915-6d0c4f3e8b27';

/** The product ids of the recorded sales, and the category each belongs to. */
export const CATEGORY_OF: Readonly<Record<number, number>> = { 1007: 11, 1020: 12, 1051: 12 };

export const example = (service: string, name: string): string =>
  readFileSync(join(CONTRACTS, service, 'examples', `${name}.xml`), 'utf8');

/** Values that came in on a request reach a response as text, so each one is escaped. */
export const xmlText = (value: string | number): string =>
  String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');

const page = (total: number, size = 20): string =>
  `<common:PageInfo><common:page>1</common:page><common:size>${size}</common:size><common:total>${total}</common:total></common:PageInfo>`;

export function productsXml(
  ids: number[],
  categoryOf: Readonly<Record<number, number>> = CATEGORY_OF,
): string {
  const known = ids.filter((id) => categoryOf[id] !== undefined);
  return `<Products xmlns="${NS}/products/v1" xmlns:common="${COMMON}">${known
    .map(
      (id) =>
        `<Product><product_id>${xmlText(id)}</product_id><sku>SKU-${xmlText(id)}</sku><name>Product ${xmlText(id)}</name><category_id>${xmlText(categoryOf[id] as number)}</category_id><list_price>10.00</list_price><is_active>true</is_active></Product>`,
    )
    .join('')}${page(known.length, 50)}</Products>`;
}

export function categoriesXml(
  entries: [number, string][] = [
    [9, 'Beverages'],
    [11, 'Cheese'],
    [12, 'Yogurt'],
  ],
): string {
  return `<Categories xmlns="${NS}/categories/v1" xmlns:common="${COMMON}">${entries
    .map(([id, name]) => `<Category><category_id>${id}</category_id><name>${xmlText(name)}</name></Category>`)
    .join('')}${page(entries.length, 100)}</Categories>`;
}

export function storesXml(
  entries: [number, string][] = [
    [1, 'Centro Historico'],
    [3, 'Polanco'],
  ],
): string {
  return `<Stores xmlns="${NS}/stores/v1" xmlns:common="${COMMON}">${entries
    .map(
      ([id, name]) =>
        `<Store><store_id>${id}</store_id><name>${xmlText(name)}</name><city>Mexico City</city><state>CDMX</state><is_active>true</is_active></Store>`,
    )
    .join('')}${page(entries.length, 100)}</Stores>`;
}

/** The recorded profile with figures that agree with the recorded sales net of their reversals, over the profile's window. */
export function agreeingProfile(): string {
  return example('rfm-engine', 'profile-scored')
    .replace('<r_days>30</r_days>', '<r_days>14</r_days>')
    .replace('<f_count>9</f_count>', '<f_count>1</f_count>')
    .replace('<m_total>842.50</m_total>', '<m_total>110.00</m_total>');
}

/** A sales page with the given rows and a total that says there are more. */
export function salesPage(rows: { id: number; at: string }[], total: number): string {
  return `<Sales xmlns="${NS}/sales/v1" xmlns:common="${COMMON}">${rows
    .map(
      (r) =>
        `<Sale><sale_id>${r.id}</sale_id><customer_id>${CUSTOMER}</customer_id><store_id>1</store_id><channel_id>1</channel_id><occurred_at>${xmlText(r.at)}</occurred_at><total>4.25</total><lines><product_id>1020</product_id><quantity>1</quantity><unit_price>4.25</unit_price></lines></Sale>`,
    )
    .join('')}${page(total, 100)}</Sales>`;
}

/** One sale of many lines, to read the products in batches. */
export function bigSale(lines: number): string {
  const body = Array.from(
    { length: lines },
    (_, i) =>
      `<lines><product_id>${2000 + i}</product_id><quantity>1</quantity><unit_price>1.00</unit_price></lines>`,
  ).join('');
  return `<Sales xmlns="${NS}/sales/v1" xmlns:common="${COMMON}"><Sale><sale_id>1</sale_id><customer_id>${CUSTOMER}</customer_id><store_id>1</store_id><channel_id>1</channel_id><occurred_at>2026-10-20T10:00:00Z</occurred_at><total>${lines}.00</total>${body}</Sale>${page(1, 100)}</Sales>`;
}

/** A customer with no reversal. */
export function noReversals(): string {
  return `<SaleReversals xmlns="${NS}/sales/v1" xmlns:common="${COMMON}">${page(0, 100)}</SaleReversals>`;
}

/** The documents every test of the module answers with, by what it asks for. */
export interface Answer {
  status: number;
  body: string;
}

export interface Behaviour {
  /** Replaces the answer to a path suffix, for a test of a refusal or an outage. */
  override?: Record<string, Answer>;
  profile?: string;
  ruleSet?: string;
  restrictions?: string;
  consent?: string;
  interests?: string;
}

/**
 * The answer of the services for one request, or undefined when it is none the module makes. The sales and
 * the reversals are the recorded examples of contracts/sales, so the figures a test expects are worked out from them.
 */
export function answer(url: URL, behaviour: Behaviour = {}): Answer | undefined {
  const path = url.pathname.replace(/^\/api\//, '');
  const forced = Object.entries(behaviour.override ?? {}).find(([suffix]) => path.endsWith(suffix));
  if (forced !== undefined) return forced[1];
  const ok = (body: string): Answer => ({ status: 200, body });
  if (path === 'sales/v1/sales') return ok(example('sales', 'list-sales'));
  if (path === 'sales/v1/sale-reversals') return ok(example('sales', 'list-sale-reversals'));
  if (path === 'sales/v1/channels') return ok(example('sales', 'list-channels'));
  if (path === 'products/v1/products')
    return ok(
      productsXml(
        (url.searchParams.get('ids') ?? '')
          .split(',')
          .map(Number)
          .filter((n) => Number.isInteger(n)),
      ),
    );
  if (path === 'categories/v1/categories') return ok(categoriesXml());
  if (path === 'stores/v1/stores') return ok(storesXml());
  if (/^rfm-engine\/v1\/rfm-profiles\/[^/]+$/.test(path))
    return ok(behaviour.profile ?? example('rfm-engine', 'profile-scored'));
  if (/^rfm-engine\/v1\/rfm-rule-sets\/\d+$/.test(path))
    return ok(behaviour.ruleSet ?? example('rfm-engine', 'rule-set'));
  if (path.endsWith('/restrictions'))
    return ok(behaviour.restrictions ?? example('customer-preferences', 'get-restrictions'));
  if (path.endsWith('/consent'))
    return ok(behaviour.consent ?? example('customer-preferences', 'get-consent'));
  if (path.endsWith('/interests'))
    return ok(behaviour.interests ?? example('customer-preferences', 'get-interest-profile'));
  return undefined;
}
