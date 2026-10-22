import { describe, expect, it } from 'vitest';
import { Users } from '../../src/main/users';
import {
  claims,
  errorXml,
  example,
  makeOffice,
  MemoryVault,
  tokenResult,
  type Responder,
  type Seen,
} from '../support/fake';

// User administration (F16-06, #484), against the recorded examples of contracts/users/.

const OWN_ID = '5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33'; // the sub of the fake session claims
const ALEX = '22222222-2222-4222-8222-222222222222';
const ADA = '11111111-1111-4111-8111-111111111111';
const NEW_PASSWORD = 'placeholder-new-password-456';
const CURRENT_PASSWORD = 'placeholder-current-password-123';

const vault = (): MemoryVault =>
  new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') });

/** A signed-in office whose session calls answer like auth-service and whose other calls answer with `responder`. */
async function signedIn(
  role: string,
  responder: Responder,
): Promise<{ users: Users; seen: Seen[]; office: ReturnType<typeof makeOffice>['office'] }> {
  const { office, seen } = makeOffice((r) => {
    if (r.method === 'POST' && r.url.endsWith('/v1/sessions'))
      return { status: 201, body: tokenResult('a1', 'r1') };
    if (r.url.endsWith('/v1/sessions/current'))
      return r.method === 'GET' ? { status: 200, body: claims(role) } : { status: 204 };
    return responder(r);
  }, vault());
  await office.signIn({ email: 'a@b.c', password: 'pw' });
  seen.length = 0;
  return { users: new Users(office), seen, office };
}

const refusal =
  (status: number, file: string, headers?: Record<string, string>): Responder =>
  () => ({ status, body: example('users', file), headers });

const paths = (seen: Seen[]): string[] =>
  seen.map((s) => `${s.method} ${new URL(s.url).pathname}${new URL(s.url).search}`);

describe('reading users and roles', () => {
  it('lists with role, active, q and paging, and reads the XML into views', async () => {
    const { users, seen } = await signedIn('AUDITOR', () => ({
      status: 200,
      body: example('users', 'users'),
    }));
    const res = await users.listUsers({ role: 'ADMIN', active: true, q: ' ada ', page: 2, size: 50 });
    expect(paths(seen)).toEqual(['GET /api/users/v1/users?role=ADMIN&active=true&q=ada&page=2&size=50']);
    expect(res.ok && res.value.items.map((u) => [u.user_id, u.role_code, u.is_active])).toEqual([
      [ADA, 'ADMIN', true],
      [ALEX, 'ANALYST', true],
    ]);
    expect(res.ok && res.value.page).toEqual({ page: 1, size: 20, total: 2 });
  });

  it('reads one user and the role vocabulary', async () => {
    const { users } = await signedIn('ADMIN', (r) =>
      r.url.includes('/roles')
        ? { status: 200, body: example('users', 'roles') }
        : { status: 200, body: example('users', 'user') },
    );
    const one = await users.getUser(ALEX);
    expect(one.ok && one.value).toMatchObject({ name: 'Alex Analyst', role_code: 'ANALYST' });
    const roles = await users.listRoles();
    expect(roles.ok && roles.value.map((r) => r.role_code)).toEqual([
      'ADMIN',
      'ANALYST',
      'STORE_MANAGER',
      'MARKETING',
      'INVENTORY_PLANNER',
      'AUDITOR',
      'CUSTOMER',
    ]);
  });

  it('refuses a malformed id or role before sending', async () => {
    const { users, seen } = await signedIn('ADMIN', refusal(500, 'invalid-body'));
    expect(await users.getUser('../x')).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(await users.listUsers({ role: 'admin; drop' })).toMatchObject({ ok: false });
    expect(seen).toHaveLength(0);
  });

  it('passes a refusal on as returned, with its correlation id', async () => {
    const { users } = await signedIn('ANALYST', refusal(422, 'invalid-body'));
    const res = await users.listUsers({});
    expect(res).toMatchObject({ ok: false, error: { code: 'invalid_body', correlation_id: 'corr-418' } });
  });
});

describe('creating and editing', () => {
  it('creates a user with the recorded request body', async () => {
    const { users, seen } = await signedIn('ADMIN', () => ({
      status: 201,
      body: example('users', 'create-user'),
    }));
    const res = await users.createUser({
      name: 'Morgan Manager',
      email: 'morgan.manager@example.com',
      role_code: 'STORE_MANAGER',
      password: NEW_PASSWORD,
    });
    expect(paths(seen)).toEqual(['POST /api/users/v1/users']);
    expect(seen[0]!.body).toContain(
      '<name>Morgan Manager</name><email>morgan.manager@example.com</email><role_code>STORE_MANAGER</role_code>',
    );
    expect(seen[0]!.body).toContain(`<password>${NEW_PASSWORD}</password>`);
    expect(res.ok && res.value).toMatchObject({ role_code: 'STORE_MANAGER' });
    expect(JSON.stringify(res)).not.toContain(NEW_PASSWORD);
  });

  it('shows single_administrator, email_taken and invalid_body as returned', async () => {
    const draft = { name: 'N', email: 'n@example.com', role_code: 'ADMIN', password: NEW_PASSWORD };
    for (const [status, file, code] of [
      [409, 'single-administrator', 'single_administrator'],
      [409, 'email-taken', 'email_taken'],
      [422, 'invalid-body', 'invalid_body'],
    ] as const) {
      const { users } = await signedIn('ADMIN', refusal(status, file));
      expect(await users.createUser(draft)).toMatchObject({ ok: false, error: { code } });
    }
    const { users } = await signedIn('ADMIN', refusal(422, 'invalid-body'));
    const res = await users.createUser(draft);
    expect(!res.ok && res.error.details).toEqual([
      { field: 'email', code: 'invalid_format' },
      { field: 'password', code: 'min_length' },
    ]);
  });

  it('refuses a missing or short password and a bad email before sending, naming the fields', async () => {
    const { users, seen } = await signedIn('ADMIN', refusal(500, 'invalid-body'));
    const res = await users.createUser({ name: 'N', email: 'nope', role_code: 'ANALYST', password: 'short' });
    expect(!res.ok && res.error.details).toEqual([
      { field: 'email', code: 'invalid_format' },
      { field: 'password', code: 'min_length' },
    ]);
    const none = await users.createUser({ name: 'N', email: 'n@example.com', role_code: 'ANALYST' });
    expect(!none.ok && none.error.details).toEqual([{ field: 'password', code: 'required' }]);
    expect(seen).toHaveLength(0);
  });

  it('edits only the changed fields, in the order the schema fixes', async () => {
    const { users, seen } = await signedIn('ADMIN', () => ({
      status: 200,
      body: example('users', 'update-user'),
    }));
    await users.updateUser(ALEX, {
      role_code: 'ANALYST',
      name: 'Alex Senior Analyst',
      email: 'alex.senior@example.com',
    });
    expect(seen[0]!.method).toBe('PATCH');
    expect(seen[0]!.body).toContain(
      '<name>Alex Senior Analyst</name><email>alex.senior@example.com</email><role_code>ANALYST</role_code>',
    );
    await users.updateUser(ALEX, { role_code: 'MARKETING' });
    expect(seen[1]!.body).toContain('<role_code>MARKETING</role_code>');
    expect(seen[1]!.body).not.toContain('<name>');
    expect(await users.updateUser(ALEX, {})).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
  });

  it('shows use_administrator_transfer as returned when the ADMIN role is forced by hand', async () => {
    const { users } = await signedIn('ADMIN', refusal(409, 'use-administrator-transfer'));
    expect(await users.updateUser(ALEX, { role_code: 'ADMIN' })).toMatchObject({
      ok: false,
      error: { code: 'use_administrator_transfer' },
    });
  });
});

describe('deactivation', () => {
  it('deactivates with POST and reactivates with DELETE on the same path', async () => {
    const { users, seen } = await signedIn('ADMIN', (r) => ({
      status: 200,
      body: example('users', r.method === 'POST' ? 'deactivate-user' : 'reactivate-user'),
    }));
    const off = await users.deactivateUser(ALEX);
    const on = await users.reactivateUser(ALEX);
    expect(paths(seen)).toEqual([
      `POST /api/users/v1/users/${ALEX}/deactivation`,
      `DELETE /api/users/v1/users/${ALEX}/deactivation`,
    ]);
    expect(off.ok && off.value.is_active).toBe(false);
    expect(on.ok && on.value.is_active).toBe(true);
  });

  it('shows administrator_required as returned', async () => {
    const { users } = await signedIn('ADMIN', refusal(409, 'administrator-required'));
    expect(await users.deactivateUser(ADA)).toMatchObject({
      ok: false,
      error: { code: 'administrator_required' },
    });
  });
});

describe('passwords', () => {
  it('resets another user with no current password and never returns the password', async () => {
    const { users, seen, office } = await signedIn('ADMIN', () => ({ status: 204 }));
    const res = await users.resetUserPassword(ALEX, NEW_PASSWORD);
    expect(res).toEqual({ ok: true, value: null });
    expect(paths(seen)).toEqual([`PUT /api/users/v1/users/${ALEX}/password`]);
    expect(seen[0]!.body).toContain(`<new_password>${NEW_PASSWORD}</new_password>`);
    expect(seen[0]!.body).not.toContain('current_password');
    // Another user's reset does not end the caller's own session.
    expect(office.state().status).toBe('signed_in');
  });

  it('refuses a password shorter than 12 characters before sending, as invalid_body', async () => {
    const { users, seen } = await signedIn('ADMIN', () => ({ status: 204 }));
    expect(await users.resetUserPassword(ALEX, 'elevenchars')).toMatchObject({
      ok: false,
      error: { code: 'invalid_body', details: [{ field: 'new_password', code: 'min_length' }] },
    });
    expect(seen).toHaveLength(0);
  });

  it('shows the 422 the service answers, as returned', async () => {
    const { users } = await signedIn('ADMIN', refusal(422, 'invalid-body'));
    expect(await users.resetUserPassword(ALEX, NEW_PASSWORD)).toMatchObject({
      ok: false,
      error: { code: 'invalid_body' },
    });
  });

  it("changes the caller's own password with the current one and returns to sign-in", async () => {
    const { users, seen, office } = await signedIn('STORE_MANAGER', () => ({ status: 204 }));
    const res = await users.changeOwnPassword(CURRENT_PASSWORD, NEW_PASSWORD);
    expect(res).toEqual({ ok: true, value: null });
    expect(paths(seen)[0]).toBe(`PUT /api/users/v1/users/${OWN_ID}/password`);
    expect(seen[0]!.body).toContain(`<current_password>${CURRENT_PASSWORD}</current_password>`);
    expect(office.state()).toMatchObject({
      status: 'signed_out',
      notice: 'Password changed, sign in again.',
    });
  });

  it('shows a wrong current password as returned and stays signed in', async () => {
    const { users, office } = await signedIn('ANALYST', refusal(422, 'invalid-current-password'));
    expect(await users.changeOwnPassword(CURRENT_PASSWORD, NEW_PASSWORD)).toMatchObject({
      ok: false,
      error: {
        code: 'invalid_current_password',
        details: [{ field: 'current_password', code: 'incorrect' }],
      },
    });
    expect(office.state().status).toBe('signed_in');
  });

  it('shows 429 rate_limited with its Retry-After', async () => {
    const { users } = await signedIn('ANALYST', refusal(429, 'rate-limited', { 'Retry-After': '540' }));
    expect(await users.changeOwnPassword(CURRENT_PASSWORD, NEW_PASSWORD)).toMatchObject({
      ok: false,
      error: { code: 'rate_limited', retry_after: '540' },
    });
  });

  it('asks for the current password before sending', async () => {
    const { users, seen } = await signedIn('ANALYST', () => ({ status: 204 }));
    const res = await users.changeOwnPassword('', NEW_PASSWORD);
    expect(!res.ok && res.error.details).toEqual([{ field: 'current_password', code: 'required' }]);
    expect(seen).toHaveLength(0);
  });
});

describe('administrator transfer', () => {
  it('sends the successor and answers 204', async () => {
    const { users, seen } = await signedIn('ADMIN', () => ({ status: 204 }));
    expect(await users.transferAdministrator(ALEX)).toEqual({ ok: true, value: null });
    expect(paths(seen)).toEqual(['POST /api/users/v1/administrator-transfers']);
    expect(seen[0]!.body).toContain(`<successor_user_id>${ALEX}</successor_user_id>`);
  });

  it('shows successor_inactive and invalid_successor as returned', async () => {
    for (const [status, file, code] of [
      [409, 'successor-inactive', 'successor_inactive'],
      [422, 'invalid-successor', 'invalid_successor'],
    ] as const) {
      const { users } = await signedIn('ADMIN', refusal(status, file));
      expect(await users.transferAdministrator(ADA)).toMatchObject({
        ok: false,
        error: { code, details: [{ field: 'successor_user_id' }] },
      });
    }
  });

  it('refuses a missing successor before sending', async () => {
    const { users, seen } = await signedIn('ADMIN', () => ({ status: 204 }));
    const res = await users.transferAdministrator('');
    expect(!res.ok && res.error.details).toEqual([{ field: 'successor_user_id', code: 'required' }]);
    expect(seen).toHaveLength(0);
  });

  it('ends the session on the next call, which is refused with session_revoked', async () => {
    let revoked = false;
    const { users, office } = await signedIn('ADMIN', (r) => {
      if (r.url.endsWith('/administrator-transfers')) {
        revoked = true;
        return { status: 204 };
      }
      return revoked
        ? { status: 401, body: errorXml('session_revoked', 'The token session is no longer live.') }
        : { status: 200, body: example('users', 'users') };
    });
    expect((await users.transferAdministrator(ALEX)).ok).toBe(true);
    expect(office.state().status).toBe('signed_in');
    expect(await users.listUsers({ role: 'ADMIN' })).toMatchObject({
      ok: false,
      error: { code: 'session_revoked' },
    });
    expect(office.state()).toMatchObject({
      status: 'signed_out',
      notice: 'Your session was revoked. Sign in again.',
    });
  });
});
