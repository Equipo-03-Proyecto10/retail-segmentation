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

  async channel(id: number): Promise<string> {
    this.channelList ??= (async () => {
      const res = await this.office.authed({ method: 'GET', path: '/v1/channels' });
      if (!res.ok) return null;
      const data = res.data?.Channels as { Channel?: { channel_id: string; name: string }[] };
      return new Map((data.Channel ?? []).map((c) => [Number(c.channel_id), c.name]));
    })();
    return (await this.channelList)?.get(id) ?? `Channel ${id} (unavailable)`;
  }
}
