import type { StoreView } from '../shared/api';
import type { Backoffice } from './backoffice';

// Names for the ids that other services return (ADR-0035: a stock level, a sale
// or an event carries ids only). One Names lives for one screen call, so each
// name is read at most once per call. A name that cannot be read is shown as
// unavailable instead of failing the screen.

/** products-service resolves at most 50 ids per call. */
export const PRODUCT_BATCH = 50;
const STORE_PAGE_SIZE = 100;
/** 32,767 stores at 100 a page; a service that never ends its pages is cut off here. */
const STORE_PAGE_LIMIT = 328;
const CATEGORY_PAGE_SIZE = 100;
/** 32,767 categories at 100 a page. */
const CATEGORY_PAGE_LIMIT = 328;

interface StoreXml {
  store_id: string;
  name: string;
  city: string;
  state: string;
  is_active: string;
}

export function storeOf(x: StoreXml): StoreView {
  return {
    store_id: Number(x.store_id),
    name: x.name,
    city: x.city,
    state: x.state,
    is_active: x.is_active === 'true' || x.is_active === '1',
  };
}

export class Names {
  private readonly productNames = new Map<number, string>();
  private storeList: Promise<StoreView[] | null> | undefined;
  private channelList: Promise<Map<number, string> | null> | undefined;
  private categoryList: Promise<Map<number, string> | null> | undefined;

  constructor(private readonly office: Backoffice) {}

  /** Reads the names of these products, 50 ids per call. */
  async products(ids: number[]): Promise<Map<number, string>> {
    const wanted = [...new Set(ids)].filter((id) => !this.productNames.has(id));
    for (let i = 0; i < wanted.length; i += PRODUCT_BATCH) {
      const batch = wanted.slice(i, i + PRODUCT_BATCH);
      const params = new URLSearchParams({ ids: batch.join(','), size: String(PRODUCT_BATCH) });
      const res = await this.office.authed({ method: 'GET', path: `/v1/products?${params.toString()}` });
      if (!res.ok) continue;
      const page = res.data?.Products as { Product?: { product_id: string; name: string }[] };
      for (const p of page.Product ?? []) this.productNames.set(Number(p.product_id), p.name);
    }
    return this.productNames;
  }

  product(id: number): string {
    return this.productNames.get(id) ?? `Product ${id} (unavailable)`;
  }

  /** Every store, reading every page once. null when the directory cannot be read. */
  stores(): Promise<StoreView[] | null> {
    this.storeList ??= (async () => {
      const all: StoreView[] = [];
      for (let page = 1; page <= STORE_PAGE_LIMIT; page += 1) {
        const params = new URLSearchParams({ page: String(page), size: String(STORE_PAGE_SIZE) });
        const res = await this.office.authed({ method: 'GET', path: `/v1/stores?${params.toString()}` });
        if (!res.ok) return null;
        const data = res.data?.Stores as { Store?: StoreXml[]; PageInfo: { total: string } };
        const items = (data.Store ?? []).map(storeOf);
        all.push(...items);
        if (items.length === 0 || all.length >= Number(data.PageInfo.total)) break;
      }
      return all;
    })();
    return this.storeList;
  }

  async store(id: number): Promise<string> {
    const stores = await this.stores();
    return stores?.find((s) => s.store_id === id)?.name ?? `Store ${id} (unavailable)`;
  }

  /** Every channel, in the order sales-service returns them. null when it cannot be read. */
  channels(): Promise<Map<number, string> | null> {
    this.channelList ??= (async () => {
      const res = await this.office.authed({ method: 'GET', path: '/v1/channels' });
      if (!res.ok) return null;
      const data = res.data?.Channels as { Channel?: { channel_id: string; name: string }[] };
      return new Map((data.Channel ?? []).map((c) => [Number(c.channel_id), c.name]));
    })();
    return this.channelList;
  }

  async channel(id: number): Promise<string> {
    return (await this.channels())?.get(id) ?? `Channel ${id} (unavailable)`;
  }

  /** Every category, reading every page once. null when categories-service cannot be read. */
  categories(): Promise<Map<number, string> | null> {
    this.categoryList ??= (async () => {
      const all = new Map<number, string>();
      for (let page = 1; page <= CATEGORY_PAGE_LIMIT; page += 1) {
        const params = new URLSearchParams({ page: String(page), size: String(CATEGORY_PAGE_SIZE) });
        const res = await this.office.authed({ method: 'GET', path: `/v1/categories?${params.toString()}` });
        if (!res.ok) return null;
        const data = res.data?.Categories as {
          Category?: { category_id: string; name: string }[];
          PageInfo: { total: string };
        };
        const items = data.Category ?? [];
        for (const c of items) all.set(Number(c.category_id), c.name);
        if (items.length === 0 || all.size >= Number(data.PageInfo.total)) break;
      }
      return all;
    })();
    return this.categoryList;
  }

  async category(id: number): Promise<string> {
    return (await this.categories())?.get(id) ?? `Category ${id} (unavailable)`;
  }
}
