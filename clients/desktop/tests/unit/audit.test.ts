import { describe, expect, it } from 'vitest';
import { Audit } from '../../src/main/audit';
import { bundle, claims, example, makeOffice, MemoryVault, tokenResult } from '../support/fake';
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

// The domain events (F28-15, #884, ADR-0072), against the recorded examples of
// contracts/audit-trail/. The service answers XML and the response is validated against the
// XSD, so a key outside its kind never reaches a screen.

const events: Responder = () => ({ status: 200, body: example('audit-trail', 'domain-events-page') });
const ACTOR = '7c2e5a90-1b3d-4f68-8a47-d09e3c5b1f72';

describe('listing domain events', () => {
  it('sends every filter in the contract order and reads the page of every kind', async () => {
    const { audit, seen } = await signedIn('AUDITOR', events);
    const res = await audit.listEvents({
      kind: 'CRITICAL_ACTION',
      actor_user_id: ACTOR,
      actor_role: ' auditor-not ',
      page: 2,
    });
    // A lower-case role is refused, so nothing was sent for the call above.
    expect(res).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(seen).toHaveLength(0);

    const all = await audit.listEvents({
      kind: 'CRITICAL_ACTION',
      actor_user_id: ACTOR,
      actor_role: 'AUDITOR',
      model_version_id: '9',
      subject_type: 'CAMPAIGN',
      subject_id: ' 12 ',
      store_id: 3,
      outcome: 'SUCCESS',
      source: 'svc_campaigns',
      from: '2026-11-01T00:00:00+01:00',
      to: '2026-11-09T23:59:59Z',
      page: 2,
      size: 50,
    });
    expect(urls(seen)).toEqual([
      'GET /api/audit-trail/v1/domain-events?kind=CRITICAL_ACTION&actor_user_id=7c2e5a90-1b3d-4f68-8a47-d09e3c5b1f72&actor_role=AUDITOR&model_version_id=9&subject_type=CAMPAIGN&subject_id=12&store_id=3&outcome=SUCCESS&source=svc_campaigns&from=2026-11-01T00%3A00%3A00%2B01%3A00&to=2026-11-09T23%3A59%3A59Z&page=2&size=50',
    ]);
    expect(seen[0]?.headers.Accept).toBe('application/xml');
    expect(all.ok).toBe(true);
    if (!all.ok) return;
    expect(all.value.page).toEqual({ page: 1, size: 20, total: 11 });
    // Newest first, as recorded: the list read of the auditor, then the single read before it.
    expect(all.value.items.map((e) => e.kind)).toEqual([
      'AUDIT_READ',
      'AUDIT_READ',
      'HISTORY_READ',
      'HISTORY_READ',
      'CRITICAL_ACTION',
      'PERMISSION_CHANGED',
      'PERMISSION_CHANGED',
      'MODEL_ROLLED_BACK',
      'MODEL_PUBLISHED',
      'CALCULATION_RUN',
      'CALCULATION_RUN',
    ]);
  });

  it('keeps who, what and under which version, and leaves an absent member null', async () => {
    const { audit } = await signedIn('ADMIN', events);
    const res = await audit.listEvents({});
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    const byKind = (kind: string, n = 0): (typeof res.value.items)[number] =>
      res.value.items.filter((e) => e.kind === kind)[n] as never;

    const listRead = byKind('AUDIT_READ', 0);
    expect(listRead).toMatchObject({
      actor_user_id: ACTOR,
      actor_role: 'AUDITOR',
      source: 'svc_audit_trail',
      subject_type: 'DOMAIN_EVENT',
      subject_id: null,
      model_version_id: null,
      rule_set_version: null,
      store_id: null,
      details: [{ key: 'filters', value: 'from, kind' }],
    });
    expect(byKind('AUDIT_READ', 1).details).toEqual([]);

    const rolledBack = byKind('MODEL_ROLLED_BACK');
    expect(rolledBack).toMatchObject({ model_version_id: '8', subject_id: '8', actor_role: 'ADMIN' });
    expect(rolledBack.details).toEqual([
      { key: 'previous_version_id', value: '9' },
      { key: 'reason_code', value: 'SEGMENTS_UNSTABLE' },
    ]);

    // A run under a rule set names it; a failed run names its model version and its parameters.
    const runs = res.value.items.filter((e) => e.kind === 'CALCULATION_RUN');
    expect(runs.map((e) => [e.rule_set_version, e.model_version_id])).toEqual([
      [null, '9'],
      ['4', null],
    ]);
    expect(runs[0]?.details.find((d) => d.key === 'status')?.value).toBe('FAILED');
    expect(runs[0]?.actor_user_id).toBeNull();
    expect(runs[0]?.actor_type).toBe('SYSTEM');

    const stores = res.value.items.filter((e) => e.kind === 'PERMISSION_CHANGED')[0];
    expect(stores?.details.find((d) => d.key === 'store_ids')?.value).toBe('3, 7');
    expect(stores?.details.map((d) => d.key)).toEqual(['change', 'subject_user_id', 'store_ids']);

    const refused = res.value.items.filter((e) => e.kind === 'HISTORY_READ')[0];
    expect(refused).toMatchObject({ outcome: 'REFUSED', actor_role: 'INVENTORY_PLANNER' });
    // The entry carries the chain of the trail.
    expect(refused?.entry_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(refused?.prev_hash).toMatch(/^[0-9a-f]{64}$/);
  });

  it('refuses a bad filter before sending', async () => {
    const { audit, seen } = await signedIn('ADMIN', events);
    for (const q of [
      { kind: 'ACCOUNT_DELETED' },
      { kind: 'critical_action' },
      { outcome: 'FAILED' },
      { subject_type: 'ORDER' },
      { actor_user_id: 'x' },
      { actor_role: 'auditor' },
      { actor_role: 'A'.repeat(41) },
      { model_version_id: '0' },
      { model_version_id: '1.5' },
      { model_version_id: '9223372036854775808' },
      { subject_id: 'x'.repeat(81) },
      { subject_id: 7 },
      { store_id: 0 },
      { store_id: 32768 },
      { source: 'SVC_MODELS' },
      { source: 'svc models' },
      { from: '2026-11-01' },
      { to: 'yesterday' },
      { size: 101 },
      { page: 0 },
      'text',
    ])
      expect(await audit.listEvents(q)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(seen).toHaveLength(0);
  });

  it('shows invalid_query as returned, with the parameter it names', async () => {
    const { audit } = await signedIn('ADMIN', () => ({
      status: 422,
      body: example('audit-trail', 'domain-event-invalid-query'),
    }));
    expect(await audit.listEvents({})).toMatchObject({
      ok: false,
      error: {
        code: 'invalid_query',
        correlation_id: 'corr-event-422',
        details: [{ field: 'kind', code: 'enum' }],
      },
    });
  });

  it('shows an answer outside the contract as a defect and uses none of it', async () => {
    // A details key the kind does not list, here a credential: the XSD refuses the document.
    for (const name of [
      'domain-event-credential-key.invalid',
      'domain-event-foreign-details-key.invalid',
      'domain-event-unknown-kind.invalid',
    ]) {
      const { audit } = await signedIn('ADMIN', () => ({ status: 200, body: example('audit-trail', name) }));
      const res = await audit.getEvent('fec1df3c-ad60-5abe-8b63-575d011303a4');
      expect(res).toMatchObject({ ok: false, error: { code: 'contract_defect' } });
      if (!res.ok) expect(JSON.stringify(res.error).toLowerCase()).not.toMatch(/password|argon2/);
    }
  });
});

describe('one domain event', () => {
  const ONE = [
    ['domain-event-calculation-run', 'CALCULATION_RUN'],
    ['domain-event-calculation-run-failed', 'CALCULATION_RUN'],
    ['domain-event-model-published', 'MODEL_PUBLISHED'],
    ['domain-event-model-rolled-back', 'MODEL_ROLLED_BACK'],
    ['domain-event-permission-role', 'PERMISSION_CHANGED'],
    ['domain-event-permission-stores', 'PERMISSION_CHANGED'],
    ['domain-event-critical-action', 'CRITICAL_ACTION'],
    ['domain-event-history-read', 'HISTORY_READ'],
    ['domain-event-history-read-refused', 'HISTORY_READ'],
    ['domain-event-audit-read', 'AUDIT_READ'],
  ] as const;

  it.each(ONE)('reads %s by its id', async (name, kind) => {
    const { audit, seen } = await signedIn('AUDITOR', () => ({
      status: 200,
      body: example('audit-trail', name),
    }));
    const id = 'fec1df3c-ad60-5abe-8b63-575d011303a4';
    const res = await audit.getEvent(id);
    expect(urls(seen)).toEqual([`GET /api/audit-trail/v1/domain-events/${id}`]);
    expect(res.ok && res.value.kind).toBe(kind);
  });

  it('reads the owner and the version of a publication', async () => {
    const { audit } = await signedIn('ADMIN', () => ({
      status: 200,
      body: example('audit-trail', 'domain-event-model-published'),
    }));
    const res = await audit.getEvent('3a5c8d1e-0b47-5c2f-9e6a-1d4b7f2c8e90');
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.value).toMatchObject({
      actor_user_id: '11111111-1111-1111-1111-000000000001',
      actor_role: 'ADMIN',
      source: 'svc_models',
      model_version_id: '9',
      subject_type: 'MODEL_VERSION',
      details: [
        { key: 'previous_version_id', value: '8' },
        { key: 'reason_code', value: 'DRIFT_REVIEW' },
      ],
    });
  });

  it('shows an unknown id as domain_event_not_found, as returned', async () => {
    const { audit } = await signedIn('ADMIN', () => ({
      status: 404,
      body: example('audit-trail', 'domain-event-not-found'),
    }));
    expect(await audit.getEvent('3a5c8d1e-0b47-5c2f-9e6a-1d4b7f2c8e90')).toMatchObject({
      ok: false,
      error: { code: 'domain_event_not_found', correlation_id: 'corr-event-404' },
    });
  });

  it('refuses an id that is not a UUID before sending', async () => {
    const { audit, seen } = await signedIn('ADMIN', events);
    for (const v of ['1', 'verification', '../audit-entries', '3a5c8d1e-0b47-5c2f-9e6a', 12, '', undefined])
      expect(await audit.getEvent(v)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(seen).toHaveLength(0);
  });
});

describe('who reaches the audit module', () => {
  const calls: [string, (a: Audit) => Promise<unknown>][] = [
    ['listEntries', (a) => a.listEntries({})],
    ['getEntry', (a) => a.getEntry('1198')],
    ['listEvents', (a) => a.listEvents({})],
    ['getEvent', (a) => a.getEvent('fec1df3c-ad60-5abe-8b63-575d011303a4')],
  ];

  it('is allowed to exactly ADMIN and AUDITOR by the contract, for every operation', () => {
    const ops = bundle.operations.filter((o) => o.service === 'audit-trail' && o.path.startsWith('/v1/'));
    expect(ops.map((o) => o.operationId).sort()).toEqual([
      'getAuditEntry',
      'getDomainEvent',
      'listAuditEntries',
      'listDomainEvents',
      'verifyDomainEvents',
    ]);
    for (const o of ops) expect(o.roles, o.operationId).toEqual(['ADMIN', 'AUDITOR']);
  });

  // The three floor roles of ADR-0060 are not in the auth contract of this branch yet, so they
  // cannot sign in here; the contract check above covers them, since no operation lists them.
  it.each(['ANALYST', 'MARKETING', 'STORE_MANAGER', 'INVENTORY_PLANNER'])(
    'refuses %s in the main process, and sends nothing',
    async (role) => {
      const { audit, seen } = await signedIn(role, events);
      for (const [name, call] of calls)
        expect(await call(audit), name).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
      expect(seen).toHaveLength(0);
    },
  );

  it.each(['ADMIN', 'AUDITOR'])('lets %s read both resources', async (role) => {
    const { audit, seen } = await signedIn(role, (r) =>
      r.url.includes('/domain-events/')
        ? { status: 200, body: example('audit-trail', 'domain-event-audit-read') }
        : r.url.includes('/domain-events')
          ? { status: 200, body: example('audit-trail', 'domain-events-page') }
          : r.url.includes('/audit-entries/')
            ? { status: 200, body: example('audit-trail', 'audit-entry-insert') }
            : { status: 200, body: example('audit-trail', 'audit-entries') },
    );
    for (const [name, call] of calls) expect(await call(audit), name).toMatchObject({ ok: true });
    expect(seen).toHaveLength(4);
  });

  it('refuses every call when signed out, and sends nothing', async () => {
    const { office, seen } = makeOffice(events);
    const audit = new Audit(office);
    for (const [name, call] of calls)
      expect(await call(audit), name).toMatchObject({ ok: false, error: { code: 'signed_out' } });
    expect(seen).toHaveLength(0);
  });
});
