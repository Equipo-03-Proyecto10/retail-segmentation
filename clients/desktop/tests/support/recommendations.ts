// Pages the recommendations tests need from the services that own names (ADR-0035): products,
// categories, stores, channels and customers, in the shape their XSDs accept. The recommendations
// answers themselves are the recorded examples of contracts/recommendations/examples/v2/.

const NS = 'https://mosaiq.maxthecoder.online/xml';
const COMMON = `${NS}/common/v1`;

/** XML text for a value a test put in a name, so a name can carry markup and stay one string. */
export const escapeXml = (text: string): string =>
  text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');

const page = (total: number): string =>
  `<common:PageInfo><common:page>1</common:page><common:size>100</common:size><common:total>${total}</common:total></common:PageInfo>`;

export const productsPage = (ids: number[], name: (id: number) => string = (id) => `Item ${id}`): string =>
  `<Products xmlns="${NS}/products/v1" xmlns:common="${COMMON}">${ids
    .map(
      (id) =>
        `<Product><product_id>${id}</product_id><sku>MOS-${id}</sku><name>${escapeXml(name(id))}</name><category_id>12</category_id><list_price>10.00</list_price><is_active>true</is_active></Product>`,
    )
    .join('')}${page(ids.length)}</Products>`;

export const categoriesPage = (categories: [number, string][]): string =>
  `<Categories xmlns="${NS}/categories/v1" xmlns:common="${COMMON}">${categories
    .map(
      ([id, name]) => `<Category><category_id>${id}</category_id><name>${escapeXml(name)}</name></Category>`,
    )
    .join('')}${page(categories.length)}</Categories>`;

export const storesPage = (stores: [number, string][]): string =>
  `<Stores xmlns="${NS}/stores/v1" xmlns:common="${COMMON}">${stores
    .map(
      ([id, name]) =>
        `<Store><store_id>${id}</store_id><name>${escapeXml(name)}</name><city>Mexico City</city><state>CDMX</state><is_active>true</is_active></Store>`,
    )
    .join('')}${page(stores.length)}</Stores>`;

export const channelsPage = (channels: [number, string][]): string =>
  `<Channels xmlns="${NS}/sales/v1" xmlns:common="${COMMON}">${channels
    .map(([id, name]) => `<Channel><channel_id>${id}</channel_id><name>${escapeXml(name)}</name></Channel>`)
    .join('')}</Channels>`;

export const customersPage = (customers: [string, string, string][]): string =>
  `<Customers xmlns="${NS}/customers/v1" xmlns:common="${COMMON}">${customers
    .map(
      ([id, name, email]) =>
        `<Customer><customer_id>${id}</customer_id><name>${escapeXml(name)}</name><email>${escapeXml(email)}</email><registration_channel_id>1</registration_channel_id><registered_on>2025-11-08</registered_on></Customer>`,
    )
    .join('')}${page(customers.length)}</Customers>`;

/** The stores and channels the v2 examples refer to: store 3 and 5, channel 1. */
export const STORES: [number, string][] = [
  [3, 'Coyoacan'],
  [5, 'Polanco'],
];
export const CHANNELS_LIST: [number, string][] = [
  [1, 'mobile_app'],
  [2, 'web'],
];
export const CATEGORIES: [number, string][] = [
  [7, 'Dairy'],
  [9, 'Bakery'],
  [12, 'Yogurt'],
];

/** The customer the v2 examples are about. */
export const CUSTOMER = '3f6c1d52-8e4a-4b7d-a0c9-5d2e7b1f4a68';
/** The decision of examples/v2/recommended-with-alternatives and decision. */
export const DECISION = 'c41e8a06-72d3-4b95-a1f0-6e2b9d57c3a4';
