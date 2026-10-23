import type { Paged, Result, StoreDetailView, StoreLocationView } from '../shared/api';
import type { Backoffice } from './backoffice';
import {
  checked,
  failed,
  FieldChecks,
  id,
  invalidInput,
  optionalBoolean,
  optionalText,
  pageNumber,
  pageOf,
  pageSize,
  record,
} from './checks';
import { storeOf } from './names';
import type { CallResult } from './transport';

// Store locations and geofences (F16-07, #485). Every value from the screens is
// checked here before a path or a body is built from it. Bounds are the XSD's
// own facets (stores.xsd), checked first so a refusal names its field; the XSD
// stays the last gate in the transport.

const MAX_STORE_ID = 32767;
const CITY_MAX = 200;
const DECIMAL = /^[+-]?(\d+(\.\d*)?|\.\d+)$/;
const INTEGER = /^[+-]?\d+$/;

interface LocationXml {
  latitude: string;
  longitude: string;
  geofence_radius_m: string;
  set_at: string;
}

type StoreXml = Parameters<typeof storeOf>[0] & { location?: LocationXml };

function locationOf(x: LocationXml): StoreLocationView {
  return {
    latitude: x.latitude,
    longitude: x.longitude,
    geofence_radius_m: Number(x.geofence_radius_m),
    set_at: x.set_at,
  };
}

function detailOf(x: StoreXml): StoreDetailView {
  return { ...storeOf(x), location: x.location === undefined ? null : locationOf(x.location) };
}

/** A decimal within [min, max], as the XSD facet defines it; the text is sent as typed. */
function decimal(f: FieldChecks, field: string, value: unknown, min: number, max: number): string {
  if (typeof value !== 'string' || value.trim() === '') {
    f.details.push({ field, code: 'required' });
    return '';
  }
  const text = value.trim();
  if (!DECIMAL.test(text)) {
    f.details.push({ field, code: 'invalid_format' });
    return '';
  }
  const n = Number(text);
  if (n < min || n > max) f.details.push({ field, code: 'out_of_range' });
  return text;
}

function radius(f: FieldChecks, field: string, value: unknown): string {
  if (typeof value !== 'string' || value.trim() === '') {
    f.details.push({ field, code: 'required' });
    return '';
  }
  const text = value.trim();
  if (!INTEGER.test(text)) {
    f.details.push({ field, code: 'invalid_format' });
    return '';
  }
  const n = Number(text);
  if (n < 25 || n > 2000) f.details.push({ field, code: 'out_of_range' });
  return text;
}

export class Stores {
  constructor(private readonly office: Backoffice) {}

  private run<T>(work: () => Promise<Result<T>>): Promise<Result<T>> {
    return checked(this.office, work);
  }

  listStores(query: unknown): Promise<Result<Paged<StoreDetailView>>> {
    return this.run(async () => {
      const q = record(query ?? {}, 'The search');
      const params = new URLSearchParams();
      const active = optionalBoolean(q.active, 'Active');
      if (active !== undefined) params.set('active', String(active));
      const city = optionalText(q.city, 'The city');
      if (city?.trim()) {
        if (city.length > CITY_MAX) throw invalidInput('The city is too long.');
        params.set('city', city.trim());
      }
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.office.authed({ method: 'GET', path: `/v1/stores?${params.toString()}` });
      if (!res.ok) return failed(res);
      const data = res.data?.Stores as { Store?: StoreXml[] } & Record<string, unknown>;
      return { ok: true, value: { items: (data.Store ?? []).map(detailOf), page: pageOf(data) } };
    });
  }

  getStore(storeId: unknown): Promise<Result<StoreDetailView>> {
    return this.run(async () => {
      const sid = id(storeId, MAX_STORE_ID, 'The store');
      const res = await this.office.authed({ method: 'GET', path: `/v1/stores/${encodeURIComponent(sid)}` });
      return this.storeResult(res);
    });
  }

  /** The only write for store coordinates: PUT .../location (ADR-0035). */
  setLocation(storeId: unknown, location: unknown): Promise<Result<StoreDetailView>> {
    return this.run(async () => {
      const sid = id(storeId, MAX_STORE_ID, 'The store');
      const l = record(location, 'The location');
      const f = new FieldChecks();
      const fields = {
        latitude: decimal(f, 'latitude', l.latitude, -90, 90),
        longitude: decimal(f, 'longitude', l.longitude, -180, 180),
        geofence_radius_m: radius(f, 'geofence_radius_m', l.geofence_radius_m),
      };
      f.done();
      const res = await this.office.authed({
        method: 'PUT',
        path: `/v1/stores/${encodeURIComponent(sid)}/location`,
        body: { root: 'LocationUpdate', fields },
      });
      return this.storeResult(res);
    });
  }

  removeLocation(storeId: unknown): Promise<Result<null>> {
    return this.run(async () => {
      const sid = id(storeId, MAX_STORE_ID, 'The store');
      const res = await this.office.authed({
        method: 'DELETE',
        path: `/v1/stores/${encodeURIComponent(sid)}/location`,
      });
      if (!res.ok) return failed(res);
      return { ok: true, value: null };
    });
  }

  private storeResult(res: CallResult): Result<StoreDetailView> {
    if (!res.ok) return failed(res);
    return { ok: true, value: detailOf(res.data?.Store as StoreXml) };
  }
}
