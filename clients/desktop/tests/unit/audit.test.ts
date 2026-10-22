import { describe, expect, it } from 'vitest';
import { Audit } from '../../src/main/audit';
import { claims, example, makeOffice, MemoryVault, tokenResult } from '../support/fake';
import type { Responder, Seen } from '../support/fake';

// The audit trail (F16-09, #487), against the recorded examples of contracts/audit-trail/.

async function signedIn(role: string, responder: Responder): Promise<{ audit: Audit; seen: Seen[] }> {
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
  return { audit: new Audit(office), seen };
}

const list: Responder = () => ({ status: 200, body: example('audit-trail', 'audit-entries') });
const urls = (seen: Seen[]): string[] =>
  seen.map((s) => `${s.method} ${new URL(s.url).pathname}${new URL(s.url).search}`);

describe('listing', () => {
  it('sends every filter and reads the entries, omitting absent members as null', async () => {
    const { audit, seen } = await signedIn('AUDITOR', list);
    const res = await audit.listEntries({
      entity: 'app_user',
      entity_pk: ' 4f6c ',
      user_id: '11111111-1111-1111-1111-000000000001',
      action: 'UPDATE',
      from: '2026-10-16T14:00:00+02:00',
      to: '2026-10-16T15:00:00Z',
      page: 2,
      size: 50,
    });
    expect(urls(seen)).toEqual([
      'GET /api/audit-trail/v1/audit-entries?entity=app_user&entity_pk=4f6c&user_id=11111111-1111-1111-1111-000000000001&action=UPDATE&from=2026-10-16T14%3A00%3A00%2B02%3A00&to=2026-10-16T15%3A00%3A00Z&page=2&size=50',
    ]);
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    const [update, , del] = res.value.items;
    expect(update).toMatchObject({ audit_id: '1198', user_id: '11111111-1111-1111-1111-000000000001' });
    expect(update?.data_before).toContain('"is_active":true');
    expect(del).toMatchObject({ entity: 'category', user_id: null, data_after: null });
  });

  it('refuses a bad filter before sending', async () => {
    const { audit, seen } = await signedIn('ADMIN', list);
    for (const q of [
      { entity: 'nope' },
      { action: 'TRUNCATE' },
      { user_id: 'x' },
      { from: '2026-10-16' },
      { to: 'yesterday' },
      { entity_pk: 'x'.repeat(81) },
      { size: 101 },
      { page: 0 },
      'text',
    ])
      expect(await audit.listEntries(q)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(seen).toHaveLength(0);
  });

  it('shows invalid_query and forbidden_role as returned', async () => {
    const a = await signedIn('ADMIN', () => ({ status: 422, body: example('audit-trail', 'invalid-query') }));
    expect(await a.audit.listEntries({})).toMatchObject({
      ok: false,
      error: { code: 'invalid_query', details: [{ field: 'entity', code: 'enum' }] },
    });
    const b = await signedIn('MARKETING', () => ({
      status: 403,
      body: '<Error xmlns="https://mosaiq.maxthecoder.online/xml/common/v1"><code>forbidden_role</code><message>m</message><correlation_id>c</correlation_id></Error>',
    }));
    expect(await b.audit.listEntries({})).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  });
});

describe('one entry', () => {
  it('reads an entry by its id', async () => {
    const { audit, seen } = await signedIn('AUDITOR', () => ({
      status: 200,
      body: example('audit-trail', 'audit-entry-app-user-update'),
    }));
    const res = await audit.getEntry('1198');
    expect(urls(seen)).toEqual(['GET /api/audit-trail/v1/audit-entries/1198']);
    expect(res.ok && res.value.action).toBe('UPDATE');
  });

  it('shows an unknown id as audit_entry_not_found, as returned', async () => {
    const { audit } = await signedIn('ADMIN', () => ({
      status: 404,
      body: example('audit-trail', 'audit-entry-not-found'),
    }));
    expect(await audit.getEntry('99999')).toMatchObject({
      ok: false,
      error: { code: 'audit_entry_not_found', correlation_id: 'corr-audit-404' },
    });
  });

  it('refuses an id that is not a positive 64-bit integer before sending', async () => {
    const { audit, seen } = await signedIn('ADMIN', list);
    for (const v of ['0', '-1', '1/../2', '1.5', '9223372036854775808', 12, '']) {
      expect(await audit.getEntry(v)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    }
    expect(seen).toHaveLength(0);
  });
});
