import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { Stores } from '../../src/main/stores';
import { XsdValidator } from '../../src/main/xml';
import { InvalidRequest } from '../../src/main/transport';
import {
  bundle,
  claims,
  example,
  makeOffice,
  makeTransport,
  MemoryVault,
  tokenResult,
} from '../support/fake';
import type { Responder, Seen } from '../support/fake';

// Store locations and geofences (F16-07, #485), against the recorded examples of contracts/stores/.

const STORES_NS = 'https://mosaiq.maxthecoder.online/xml/stores/v1';
const GOOD = { latitude: '19.432608', longitude: '-99.133209', geofence_radius_m: '150' };

async function signedIn(role: string, responder: Responder): Promise<{ stores: Stores; seen: Seen[] }> {
  const { office, seen } = makeOffice(
    (r) => {
      if (r.method === 'POST' && r.url.endsWith('/v1/sessions'))
        return { status: 201, body: tokenResult('a1', 'r1') };
      if (r.url.endsWith('/v1/sessions/current'))
        return r.method === 'GET' ? { status: 200, body: claims(role) } : { status: 204 };
      return responder(r);
    },
    new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') }),
  );
  await office.signIn({ email: 'a@b.c', password: 'pw' });
  seen.length = 0;
  return { stores: new Stores(office), seen };
}

const refusal =
  (status: number, file: string): Responder =>
  () => ({ status, body: example('stores', file) });

const paths = (seen: Seen[]): string[] =>
  seen.map((s) => `${s.method} ${new URL(s.url).pathname}${new URL(s.url).search}`);

describe('the directory', () => {
  it('lists with active, city and paging, marking a store without a location', async () => {
    const { stores, seen } = await signedIn('ANALYST', () => ({
      status: 200,
      body: example('stores', 'directory-page'),
    }));
    const res = await stores.listStores({ active: true, city: ' Mexico City ', page: 1, size: 20 });
    expect(paths(seen)).toEqual(['GET /api/stores/v1/stores?active=true&city=Mexico+City&page=1&size=20']);
    expect(res.ok && res.value.items.map((s) => [s.store_id, s.location?.geofence_radius_m ?? null])).toEqual(
      [
        [1, 150],
        [2, null],
      ],
    );
    expect(res.ok && res.value.items[0]?.location).toEqual({
      latitude: '19.432608',
      longitude: '-99.133209',
      geofence_radius_m: 150,
      set_at: '2026-10-15T18:20:00Z',
    });
  });

  it('opens one store with or without a location', async () => {
    const { stores } = await signedIn('AUDITOR', (r) =>
      r.url.endsWith('/stores/1')
        ? { status: 200, body: example('stores', 'store-with-location') }
        : { status: 200, body: example('stores', 'store-without-location') },
    );
    const withLocation = await stores.getStore(1);
    expect(withLocation.ok && withLocation.value.location?.latitude).toBe('19.432608');
    const without = await stores.getStore(2);
    expect(without.ok && without.value.location).toBeNull();
  });

  it('shows an unknown store as store_not_found, as returned', async () => {
    const { stores } = await signedIn('ADMIN', refusal(404, 'store-not-found'));
    expect(await stores.getStore(9999)).toMatchObject({
      ok: false,
      error: { code: 'store_not_found', correlation_id: 'corr-store-not-found' },
    });
  });

  it('refuses a malformed id or query before sending', async () => {
    const { stores, seen } = await signedIn('ADMIN', refusal(500, 'invalid-query'));
    expect(await stores.getStore(0)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(await stores.getStore('1/location' as unknown as number)).toMatchObject({ ok: false });
    expect(await stores.listStores({ active: 'yes' })).toMatchObject({ ok: false });
    expect(seen).toHaveLength(0);
  });
});

describe('setting a location', () => {
  it('sends latitude, longitude and radius with PUT and reads the stored location back', async () => {
    const { stores, seen } = await signedIn('ADMIN', () => ({
      status: 200,
      body: example('stores', 'set-location'),
    }));
    const res = await stores.setLocation(1, GOOD);
    expect(paths(seen)).toEqual(['PUT /api/stores/v1/stores/1/location']);
    expect(seen[0]!.body).toContain(
      '<latitude>19.432608</latitude><longitude>-99.133209</longitude><geofence_radius_m>150</geofence_radius_m>',
    );
    expect(res.ok && res.value.location).toMatchObject({ latitude: '19.432608', geofence_radius_m: 150 });
  });

  it('accepts the bounds themselves', async () => {
    const { stores, seen } = await signedIn('ADMIN', () => ({
      status: 200,
      body: example('stores', 'set-location'),
    }));
    for (const [latitude, longitude, geofence_radius_m] of [
      ['-90', '-180', '25'],
      ['90', '180', '2000'],
    ] as const) {
      expect((await stores.setLocation(1, { latitude, longitude, geofence_radius_m })).ok).toBe(true);
    }
    expect(seen).toHaveLength(2);
  });

  it.each([
    ['latitude', { ...GOOD, latitude: '91' }],
    ['latitude', { ...GOOD, latitude: '-90.000001' }],
    ['longitude', { ...GOOD, longitude: '180.5' }],
    ['longitude', { ...GOOD, longitude: '-181' }],
    ['geofence_radius_m', { ...GOOD, geofence_radius_m: '24' }],
    ['geofence_radius_m', { ...GOOD, geofence_radius_m: '2001' }],
  ])('refuses %s outside its bounds before sending, naming the field', async (field, draft) => {
    const { stores, seen } = await signedIn('ADMIN', () => ({
      status: 200,
      body: example('stores', 'set-location'),
    }));
    const res = await stores.setLocation(1, draft);
    expect(res).toMatchObject({
      ok: false,
      error: { code: 'invalid_body', details: [{ field, code: 'out_of_range' }] },
    });
    expect(seen).toHaveLength(0);
  });

  it('refuses missing and malformed values before sending', async () => {
    const { stores, seen } = await signedIn('ADMIN', refusal(500, 'invalid-body'));
    const res = await stores.setLocation(1, { latitude: '', longitude: 'west', geofence_radius_m: '1.5' });
    expect(!res.ok && res.error.details).toEqual([
      { field: 'latitude', code: 'required' },
      { field: 'longitude', code: 'invalid_format' },
      { field: 'geofence_radius_m', code: 'invalid_format' },
    ]);
    expect(seen).toHaveLength(0);
  });

  it('shows the 422 a forced request gets, with the field its details name', async () => {
    for (const [file, field] of [
      ['invalid-body-longitude', 'longitude'],
      ['invalid-body-geofence-radius-m', 'geofence_radius_m'],
    ] as const) {
      const { stores } = await signedIn('ADMIN', refusal(422, file));
      const res = await stores.setLocation(1, GOOD);
      expect(res).toMatchObject({
        ok: false,
        error: { code: 'invalid_body', details: [{ field, code: 'out_of_range' }] },
      });
    }
  });

  it('shows 403 forbidden_role to any other role, as returned', async () => {
    const { stores } = await signedIn('STORE_MANAGER', refusal(403, 'forbidden-role'));
    expect(await stores.setLocation(1, GOOD)).toMatchObject({
      ok: false,
      error: { code: 'forbidden_role' },
    });
    expect(await stores.removeLocation(1)).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  });

  it('shows store_not_found for an unknown store', async () => {
    const { stores } = await signedIn('ADMIN', refusal(404, 'store-not-found'));
    expect(await stores.setLocation(9999, GOOD)).toMatchObject({
      ok: false,
      error: { code: 'store_not_found' },
    });
  });
});

describe('removing a location', () => {
  it('sends DELETE and answers 204', async () => {
    const { stores, seen } = await signedIn('ADMIN', () => ({ status: 204 }));
    expect(await stores.removeLocation(1)).toEqual({ ok: true, value: null });
    expect(paths(seen)).toEqual(['DELETE /api/stores/v1/stores/1/location']);
    expect(seen[0]!.body).toBeUndefined();
  });

  it('shows store_not_found for an unknown store, as returned', async () => {
    const { stores } = await signedIn('ADMIN', refusal(404, 'store-not-found'));
    expect(await stores.removeLocation(9999)).toMatchObject({
      ok: false,
      error: { code: 'store_not_found', correlation_id: 'corr-store-not-found' },
    });
  });
});

describe('the XSD facets', () => {
  const read = (name: string): string =>
    readFileSync(join(__dirname, '..', '..', '..', '..', 'contracts', 'stores', 'examples', name), 'utf8');

  it('refuse the recorded out-of-range request and accept the recorded valid one', async () => {
    const validator = new XsdValidator(bundle);
    expect((await validator.validate(read('set-location.request.xml'), STORES_NS)).valid).toBe(true);
    const bad = await validator.validate(read('set-location-out-of-range.invalid.xml'), STORES_NS);
    expect(bad.valid).toBe(false);
  });

  it('stay the last gate: the transport sends nothing for a body outside them', async () => {
    const { transport, seen } = makeTransport(() => ({
      status: 200,
      body: example('stores', 'set-location'),
    }));
    await expect(
      transport.call({
        method: 'PUT',
        path: '/v1/stores/1/location',
        body: {
          root: 'LocationUpdate',
          fields: { latitude: '91', longitude: '-99.133209', geofence_radius_m: '150' },
        },
      }),
    ).rejects.toBeInstanceOf(InvalidRequest);
    expect(seen).toHaveLength(0);
  });
});
