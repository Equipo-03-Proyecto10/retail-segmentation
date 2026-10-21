import { describe, expect, it } from 'vitest';
import { claims, errorXml, makeOffice, MemoryVault, tokenResult, type Responder } from '../support/fake';

const staff: Responder = (r) => {
  if (r.method === 'POST' && r.url.endsWith('/v1/sessions'))
    return { status: 201, body: tokenResult('a1', 'r1') };
  if (r.url.endsWith('/v1/sessions/current') && r.method === 'GET')
    return { status: 200, body: claims('MARKETING') };
  if (r.method === 'DELETE') return { status: 204 };
  return { status: 404, body: errorXml('not_found') };
};
const creds = { email: 'a@b.c', password: 'pw' };

describe('sign-in and sign-out', () => {
  it('signs in, resolves the role and gates services by x-mosaiq-roles', async () => {
    const { office, vault } = makeOffice(staff);
    const res = await office.signIn(creds);
    expect(res.ok).toBe(true);
    const state = office.state();
    expect(state.status === 'signed_in' && state.role).toBe('MARKETING');
    const names = state.status === 'signed_in' ? state.services.map((s) => s.name) : [];
    expect(names).toContain('campaigns');
    expect(names).not.toContain('audit-trail'); // no MARKETING operation
    expect(vault.tokens?.refresh).toBe('r1');
  });

  it('signs a CUSTOMER out again and says the back office is for staff', async () => {
    const { office, seen, vault } = makeOffice((r) =>
      r.url.endsWith('/sessions/current') && r.method === 'GET'
        ? { status: 200, body: claims('CUSTOMER') }
        : staff(r),
    );
    const res = await office.signIn(creds);
    expect(!res.ok && res.error.message).toMatch(/staff roles/);
    expect(seen.some((s) => s.method === 'DELETE')).toBe(true);
    expect(vault.tokens).toBeNull();
    expect(office.state().status).toBe('signed_out');
  });

  it('calls DELETE /v1/sessions/current and erases tokens on sign-out', async () => {
    const { office, seen, vault } = makeOffice(staff);
    await office.signIn(creds);
    await office.signOut();
    expect(seen.at(-1)!.method).toBe('DELETE');
    expect(vault.tokens).toBeNull();
    expect(office.state().status).toBe('signed_out');
  });

  it('shows 503s as plain text without signing anything out', async () => {
    for (const [code, text] of [
      ['auth_unavailable', /authentication is unavailable/i],
      ['database_unavailable', /the service is unavailable, try again/i],
    ] as const) {
      const { office } = makeOffice(() => ({ status: 503, body: errorXml(code) }));
      const res = await office.signIn(creds);
      expect(!res.ok && res.error.message).toMatch(text);
      expect(!res.ok && res.error.code).toBe(code);
    }
  });
});

describe('refresh', () => {
  const expired = { access: 'old', refresh: 'r0', expiresAt: 0 };
  const calls = (seen: { url: string }[], suffix: string): number =>
    seen.filter((s) => s.url.endsWith(suffix)).length;

  it('runs exactly one refresh for five concurrent calls with an expired token', async () => {
    const used: string[] = [];
    const { office, seen } = makeOffice((r) => {
      if (r.url.endsWith('/sessions/refresh')) return { status: 200, body: tokenResult('new', 'r2') };
      used.push(r.headers.Authorization ?? '');
      return { status: 200, body: claims('ADMIN') };
    }, new MemoryVault(expired));
    await Promise.all(
      Array.from({ length: 5 }, () => office.authed({ method: 'GET', path: '/v1/sessions/current' })),
    );
    expect(calls(seen, '/sessions/refresh')).toBe(1);
    expect(used).toEqual(Array(5).fill('Bearer new'));
  });

  it('refreshes once on 401 invalid_token and retries each waiting call once', async () => {
    const live = { access: 'old', refresh: 'r0', expiresAt: Date.now() + 3_600_000 };
    const { office, seen } = makeOffice((r) => {
      if (r.url.endsWith('/sessions/refresh')) return { status: 200, body: tokenResult('new', 'r2') };
      return r.headers.Authorization === 'Bearer old'
        ? { status: 401, body: errorXml('invalid_token') }
        : { status: 200, body: claims('ADMIN') };
    }, new MemoryVault(live));
    const results = await Promise.all(
      Array.from({ length: 5 }, () => office.authed({ method: 'GET', path: '/v1/sessions/current' })),
    );
    expect(results.every((r) => r.ok)).toBe(true);
    expect(calls(seen, '/sessions/refresh')).toBe(1);
    expect(seen.filter((s) => s.url.endsWith('/sessions/current')).length).toBe(10);
  });

  it.each(['refresh_reused', 'invalid_refresh', 'session_expired', 'session_revoked'])(
    'erases the tokens and returns to sign-in on %s',
    async (code) => {
      const { office, vault } = makeOffice(
        (r) => (r.url.endsWith('/sessions/refresh') ? { status: 401, body: errorXml(code) } : staff(r)),
        new MemoryVault(expired),
      );
      const probe = await office.probeService('auth');
      expect(probe.ok).toBe(false);
      expect(vault.tokens).toBeNull();
      const state = office.state();
      expect(state.status === 'signed_out' && state.notice).toMatch(/sign in again/i);
    },
  );

  it.each(['auth_unavailable', 'database_unavailable'])(
    'keeps the session when refresh answers 503 %s',
    async (code) => {
      const { office, vault } = makeOffice(
        (r) => (r.url.endsWith('/sessions/refresh') ? { status: 503, body: errorXml(code) } : staff(r)),
        new MemoryVault(expired),
      );
      const probe = await office.probeService('auth');
      expect(!probe.ok && probe.error.code).toBe(code);
      expect(vault.tokens).not.toBeNull();
    },
  );
});

describe('errors', () => {
  it('shows a 403 forbidden_role from a forced call', async () => {
    const { office } = makeOffice((r) =>
      r.url.includes('/api/campaigns/')
        ? { status: 403, body: errorXml('forbidden_role', 'Role not allowed') }
        : staff(r),
    );
    await office.signIn(creds);
    const probe = await office.probeService('campaigns');
    expect(!probe.ok && probe.error.code).toBe('forbidden_role');
  });
});

describe('token storage', () => {
  const ACCESS = 'access-SECRET-7f3a';
  const REFRESH = 'refresh-SECRET-91c2';
  const secret: Responder = (r) =>
    r.method === 'POST' && r.url.endsWith('/v1/sessions')
      ? { status: 201, body: tokenResult(ACCESS, REFRESH) }
      : staff(r);

  /** Every string and byte buffer reachable from a value. */
  function reachable(
    value: unknown,
    seen = new Set<unknown>(),
    out: (string | Buffer)[] = [],
  ): (string | Buffer)[] {
    if (typeof value === 'string') out.push(value);
    else if (Buffer.isBuffer(value)) out.push(value);
    else if (value !== null && typeof value === 'object' && !seen.has(value)) {
      seen.add(value);
      for (const v of Object.values(value)) reachable(v, seen, out);
    }
    return out;
  }

  it('holds no token as plain text between calls', async () => {
    const { office, vault, seen } = makeOffice(secret);
    expect((await office.signIn(creds)).ok).toBe(true);
    for (const v of reachable(office))
      for (const token of [ACCESS, REFRESH]) expect(v.includes(token)).toBe(false);
    expect(vault.tokens?.access).toBe(ACCESS); // sealed, not lost
    expect(seen.at(-1)?.headers.Authorization).toBe(`Bearer ${ACCESS}`);
  });

  it('reports memory_only so the screens can warn, and still signs in', async () => {
    const { office } = makeOffice(secret, new MemoryVault(null, 'memory_only'));
    expect(office.state().storage).toBe('memory_only');
    expect((await office.signIn(creds)).ok).toBe(true);
    expect(office.state()).toMatchObject({ status: 'signed_in', storage: 'memory_only' });
  });
});
