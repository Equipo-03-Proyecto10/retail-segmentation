import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { parse } from 'yaml';
import { describe, expect, it } from 'vitest';
import { Audit } from '../../src/main/audit';
import { bundle, claims, example, makeOffice, MemoryVault, tokenResult } from '../support/fake';
import type { Responder, Seen } from '../support/fake';
import { eventsPage, field, read, recordedEvents } from '../support/domain-events';

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
// XSD, so a key outside its wrapper never reaches a screen.

const all = recordedEvents();
const everyEvent = eventsPage(all.map((e) => e.element));
const events: Responder = () => ({ status: 200, body: everyEvent });
const AUDITOR_ID = '11111111-1111-1111-1111-000000000006';
const ADMIN_ID = '11111111-1111-1111-1111-000000000001';
const ONE_ID = '7a0c5e1d-3b2f-4c8a-9d61-000000000002';

const byName = (name: string) => all.find((e) => e.name === name)!;

describe('listing domain events', () => {
  it('sends every filter in the contract order and reads the events of every kind', async () => {
    const { audit, seen } = await signedIn('AUDITOR', events);
    const res = await audit.listEvents({
      kind: 'CRITICAL_ACTION',
      actor_user_id: AUDITOR_ID,
      actor_role: 'AUDITOR',
      model_version_id: '9',
      subject_type: 'campaign',
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
      'GET /api/audit-trail/v1/domain-events?kind=CRITICAL_ACTION&actor_user_id=11111111-1111-1111-1111-000000000006&actor_role=AUDITOR&model_version_id=9&subject_type=campaign&subject_id=12&store_id=3&outcome=SUCCESS&source=svc_campaigns&from=2026-11-01T00%3A00%3A00%2B01%3A00&to=2026-11-09T23%3A59%3A59Z&page=2&size=50',
    ]);
    expect(seen[0]?.headers.Accept).toBe('application/xml');
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.value.page).toEqual({ page: 1, size: 20, total: 10 });
    // Newest first, as the service orders them.
    expect(res.value.items.map((e) => e.event_id)).toEqual(all.map((e) => e.id));
    expect(new Set(res.value.items.map((e) => e.kind))).toEqual(
      new Set([
        'CALCULATION_RUN',
        'MODEL_PUBLISHED',
        'MODEL_ROLLED_BACK',
        'PERMISSION_CHANGED',
        'CRITICAL_ACTION',
        'HISTORY_READ',
        'AUDIT_READ',
      ]),
    );
  });

  it('reads the page the contract itself records', async () => {
    const { audit } = await signedIn('ADMIN', () => ({ status: 200, body: read('domain-events') }));
    const res = await audit.listEvents({});
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.value.page).toEqual({ page: 1, size: 3, total: 9 });
    expect(res.value.items.map((e) => e.kind)).toEqual(['AUDIT_READ', 'HISTORY_READ', 'HISTORY_READ']);
  });

  it('keeps who, what and under which version, and leaves an absent member null', async () => {
    const { audit } = await signedIn('ADMIN', events);
    const res = await audit.listEvents({});
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    const named = (name: string) => res.value.items.find((e) => e.event_id === byName(name).id)!;

    // A read of a list names no subject, and a read of the trail records its filters, not their values.
    expect(named('domain-event-audit-read')).toMatchObject({
      actor_user_id: AUDITOR_ID,
      actor_role: 'AUDITOR',
      source: 'svc_audit_trail',
      subject_type: null,
      subject_id: null,
      model_version_id: null,
      rule_set_version: null,
      store_id: null,
      details: [
        { key: 'resource', value: 'DOMAIN_EVENTS' },
        { key: 'filter', value: 'kind, subject_type, subject_id' },
      ],
    });

    // A rollback names the version it restored and the one it replaced, and who did it.
    const rolledBack = named('domain-event-model-rolled-back');
    expect(rolledBack).toMatchObject({
      model_version_id: '11',
      subject_type: 'model_version',
      subject_id: '11',
      actor_role: 'ADMIN',
      actor_user_id: ADMIN_ID,
      source: 'svc_models',
    });
    expect(rolledBack.details).toEqual([
      { key: 'previous_version_id', value: '12' },
      { key: 'reason_code', value: 'DRIFT_ALERT' },
    ]);

    // A scheduled run has no actor and no request of a person, and names its rule set.
    const run = named('domain-event-calculation-run');
    expect(run).toMatchObject({
      actor_type: 'SYSTEM',
      actor_user_id: null,
      actor_role: null,
      rule_set_version: '3',
      model_version_id: null,
      subject_type: 'calculation',
    });
    // The window and the data origin are read as the text the service sent: the XSD is the only check
    // on the data origin's vocabulary, so a correction of it in the contract needs no change here.
    expect(run.details.map((d) => d.key)).toEqual([
      'calculation',
      'status',
      'input_count',
      'output_count',
      'window_from',
      'window_to',
      'data_origin',
    ]);
    expect(run.details.find((d) => d.key === 'data_origin')?.value).toBe('OBSERVED');

    // A repeated element is joined.
    const stores = named('domain-event-stores-assigned');
    expect(stores.details).toEqual([
      { key: 'change', value: 'STORES_ASSIGNED' },
      { key: 'store_id', value: '4, 9' },
    ]);
    const role = named('domain-event-permission-changed');
    expect(role.details).toEqual([
      { key: 'change', value: 'ROLE_ASSIGNED' },
      { key: 'from_role', value: 'ANALYST' },
      { key: 'to_role', value: 'MARKETING' },
    ]);

    // A history read records which history, never what was read; the customer is its subject.
    const refused = named('domain-event-history-read-refused');
    expect(refused).toMatchObject({
      outcome: 'REFUSED',
      actor_role: 'INVENTORY_PLANNER',
      subject_type: 'customer',
      subject_id: '00000000-0000-0000-0000-000000000007',
      details: [{ key: 'history', value: 'SEGMENT' }],
    });
    // The entry carries its place in the chain.
    expect(refused.entry_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(refused.prev_hash).toMatch(/^[0-9a-f]{64}$/);

    // A decision with a store keeps it.
    expect(named('domain-event-critical-action')).toMatchObject({
      store_id: 4,
      subject_type: 'sale_reversal',
      details: [
        { key: 'action', value: 'SALE_REVERSED' },
        { key: 'reason_code', value: 'CUSTOMER_RETURN' },
      ],
    });
  });

  it('reads an event with no correlation id', async () => {
    const element = byName('domain-event-model-published').element.replace(
      /<correlation_id>[^<]*<\/correlation_id>\s*/,
      '',
    );
    const { audit } = await signedIn('ADMIN', () => ({ status: 200, body: eventsPage([element]) }));
    const res = await audit.listEvents({});
    expect(res.ok && res.value.items[0]?.correlation_id).toBeNull();
  });

  it('refuses a bad filter before sending', async () => {
    const { audit, seen } = await signedIn('ADMIN', events);
    for (const q of [
      { kind: 'ACCOUNT_DELETED' },
      { kind: 'critical_action' },
      { outcome: 'FAILED' },
      { subject_type: 'ORDER' },
      { subject_type: 'CAMPAIGN' },
      { actor_user_id: 'x' },
      { actor_role: 'auditor' },
      { actor_role: 'A'.repeat(31) },
      { actor_role: 'A1' },
      { model_version_id: '0' },
      { model_version_id: '1.5' },
      { model_version_id: '9223372036854775808' },
      { subject_type: 'customer', subject_id: 'x'.repeat(81) },
      { subject_type: 'customer', subject_id: 7 },
      { store_id: 0 },
      { store_id: 32768 },
      { source: 'models' },
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

  it('refuses a subject id without its subject type, naming the field as the contract does', async () => {
    const { audit, seen } = await signedIn('AUDITOR', events);
    expect(await audit.listEvents({ subject_id: '7' })).toMatchObject({
      ok: false,
      error: { code: 'invalid_input', details: [{ field: 'subject_id', code: 'requires_subject_type' }] },
    });
    expect(seen).toHaveLength(0);
    const ok = await audit.listEvents({ subject_type: 'customer', subject_id: '7' });
    expect(ok.ok).toBe(true);
    expect(urls(seen)).toEqual([
      'GET /api/audit-trail/v1/domain-events?subject_type=customer&subject_id=7&page=1&size=20',
    ]);
  });

  it('shows invalid_query as returned, with the parameter it names', async () => {
    const { audit } = await signedIn('ADMIN', () => ({
      status: 422,
      body: read('domain-events-invalid-kind'),
    }));
    expect(await audit.listEvents({})).toMatchObject({
      ok: false,
      error: {
        code: 'invalid_query',
        correlation_id: 'corr-events-422',
        details: [{ field: 'kind', code: 'enum' }],
      },
    });
  });

  it('shows a read that could not be recorded as audit_unavailable, with no event', async () => {
    // The contract's own example of the 503 a read answers when its AUDIT_READ cannot be written.
    const doc = parse(
      readFileSync(
        join(__dirname, '..', '..', '..', '..', 'contracts', 'audit-trail', 'openapi.yaml'),
        'utf8',
      ),
    ) as {
      components: {
        responses: Record<
          string,
          { content: Record<string, { examples: Record<string, { value: string }> }> }
        >;
      };
    };
    const body = doc.components.responses.RecordedReadUnavailable?.content['application/xml']?.examples
      .audit_unavailable?.value as string;
    expect(body).toContain('<code>audit_unavailable</code>');
    const { audit } = await signedIn('AUDITOR', () => ({ status: 503, body }));
    for (const res of [await audit.listEvents({}), await audit.getEvent(ONE_ID), await audit.listEntries({})])
      expect(res).toMatchObject({
        ok: false,
        error: { code: 'audit_unavailable', correlation_id: 'corr-dependency-503c' },
      });
  });

  it('shows an answer outside the contract as a defect and uses none of it', async () => {
    // A key its wrapper does not list, here a credential, and a kind outside the list: the XSD refuses.
    for (const name of ['domain-event-credential.invalid', 'domain-event-kind.invalid']) {
      const { audit } = await signedIn('ADMIN', () => ({ status: 200, body: read(name) }));
      const res = await audit.getEvent(ONE_ID);
      expect(res).toMatchObject({ ok: false, error: { code: 'contract_defect' } });
      if (!res.ok) expect(JSON.stringify(res.error).toLowerCase()).not.toMatch(/password|argon2/);
      const page = await signedIn('ADMIN', () => ({
        status: 200,
        body: eventsPage([
          read(name)
            .replace(/^<\?xml[^>]*\?>\s*/, '')
            .replace(/ xmlns="[^"]*"/, ''),
        ]),
      }));
      expect(await page.audit.listEvents({})).toMatchObject({
        ok: false,
        error: { code: 'contract_defect' },
      });
    }
  });
});

describe('one domain event', () => {
  it.each(recordedEvents().map((e) => [e.name, e.kind] as const))(
    'reads %s by its id',
    async (name, kind) => {
      const document = byName(name).document;
      const { audit, seen } = await signedIn('AUDITOR', () => ({ status: 200, body: document }));
      const id = field(document, 'event_id');
      const res = await audit.getEvent(id);
      expect(urls(seen)).toEqual([`GET /api/audit-trail/v1/domain-events/${id}`]);
      expect(res.ok && res.value.kind).toBe(kind);
      expect(res.ok && res.value.event_id).toBe(id);
    },
  );

  it('reads the owner and the version of a publication', async () => {
    const { audit } = await signedIn('ADMIN', () => ({
      status: 200,
      body: read('domain-event-model-published'),
    }));
    const res = await audit.getEvent(ONE_ID);
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.value).toMatchObject({
      actor_user_id: ADMIN_ID,
      actor_role: 'ADMIN',
      source: 'svc_models',
      model_version_id: '12',
      subject_type: 'model_version',
      details: [
        { key: 'previous_version_id', value: '11' },
        { key: 'reason_code', value: 'QUARTERLY_REFIT' },
      ],
    });
  });

  it('shows an unknown id as domain_event_not_found, as returned', async () => {
    const { audit } = await signedIn('ADMIN', () => ({
      status: 404,
      body: read('domain-event-not-found'),
    }));
    expect(await audit.getEvent('3a5c8d1e-0b47-5c2f-9e6a-1d4b7f2c8e90')).toMatchObject({
      ok: false,
      error: { code: 'domain_event_not_found', correlation_id: 'corr-events-404' },
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
    ['getEvent', (a) => a.getEvent(ONE_ID)],
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

  it.each([
    'ANALYST',
    'MARKETING',
    'STORE_MANAGER',
    'INVENTORY_PLANNER',
    'SALES_ASSOCIATE',
    'LOYALTY_OFFICER',
    'COMMERCIAL_SUPERVISOR',
  ])('refuses %s in the main process, and sends nothing', async (role) => {
    const { audit, seen } = await signedIn(role, events);
    for (const [name, call] of calls)
      expect(await call(audit), name).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(seen).toHaveLength(0);
  });

  it.each(['ADMIN', 'AUDITOR'])('lets %s read both resources', async (role) => {
    const { audit, seen } = await signedIn(role, (r) =>
      r.url.includes('/domain-events/')
        ? { status: 200, body: read('domain-event-audit-read') }
        : r.url.includes('/domain-events')
          ? { status: 200, body: everyEvent }
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
