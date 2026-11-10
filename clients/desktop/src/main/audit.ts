import {
  AUDIT_ACTIONS,
  AUDITED_ENTITIES,
  DOMAIN_EVENT_KINDS,
  DOMAIN_EVENT_OUTCOMES,
  DOMAIN_EVENT_SUBJECT_TYPES,
  type AuditEntryView,
  type DomainEventView,
  type EventDetailView,
  type Paged,
  type Result,
} from '../shared/api';
import { AUDIT_OPERATIONS } from '../shared/modules';
import { localError, type Backoffice } from './backoffice';
import {
  checked,
  failed,
  id as wholeNumber,
  invalidInput,
  optionalDateTime,
  optionalText,
  optionalUuid,
  pageNumber,
  pageOf,
  pageSize,
  record,
  uuid,
} from './checks';
import type { CallResult } from './transport';

// The audit trail (F16-09, #487): read only, through audit-trail. Every filter
// is checked here before a query string is built. The trail has no write
// endpoint, so this class has no write method.
//
// It also reads the domain events (F28-15, #884, ADR-0072): the decisions the trail records besides
// a row change. Every call, for both resources, is refused here before anything is sent unless the
// contract's x-mosaiq-roles (ADMIN and AUDITOR) allow its operation to the signed-in role. The
// operations are published ahead of their implementation (ADR-0073); a service that does not
// answer them yet is shown as it answers.

const ENTITY_PK_MAX = 80;
const MAX_AUDIT_ID = 9223372036854775807n;
const MAX_STORE_ID = 32767;
const SUBJECT_ID_MAX = 80;
const ROLE_CODE = /^[A-Z][A-Z0-9_]{0,39}$/;
const SOURCE_NAME = /^[a-z][a-z0-9_]{0,39}$/;

interface EntryXml {
  audit_id: string;
  user_id?: string;
  entity: string;
  entity_pk: string;
  action: string;
  data_before?: string;
  data_after?: string;
  executed_at: string;
}

function entryOf(x: EntryXml): AuditEntryView {
  return {
    audit_id: x.audit_id,
    user_id: x.user_id ?? null,
    entity: x.entity,
    entity_pk: x.entity_pk,
    action: x.action,
    data_before: x.data_before ?? null,
    data_after: x.data_after ?? null,
    executed_at: x.executed_at,
  };
}

function oneOf(value: unknown, allowed: readonly string[], label: string): string | undefined {
  const text = optionalText(value, label);
  if (text === undefined || text === '') return undefined;
  if (!allowed.includes(text)) throw invalidInput(`${label} is not one of: ${allowed.join(', ')}.`);
  return text;
}

/** A positive 64-bit identifier, kept as text because it can exceed 2^53. */
function auditId(value: unknown): string {
  if (typeof value !== 'string' || !/^[1-9][0-9]{0,18}$/.test(value) || BigInt(value) > MAX_AUDIT_ID)
    throw invalidInput('The audit entry id must be a whole number from 1 to 9223372036854775807.');
  return value;
}

interface EventXml {
  event_id: string;
  kind: string;
  outcome: string;
  occurred_at: string;
  recorded_at: string;
  actor_type: string;
  actor_user_id?: string;
  actor_role?: string;
  source: string;
  store_id?: string;
  subject_type: string;
  subject_id?: string;
  model_version_id?: string;
  rule_set_version?: string;
  correlation_id: string;
  /** An array for the name `details`, which the parser repeats everywhere; empty text when there is none. */
  details?: unknown;
  prev_hash: string;
  entry_hash: string;
}

/**
 * The closed keys of a kind's details, as the XSD admits them, in the order the document gave. A
 * list (store_ids, filters) is joined; an absent member stays absent rather than becoming a blank.
 */
function detailsOf(raw: unknown): EventDetailView[] {
  const element = Array.isArray(raw) ? raw[0] : raw;
  if (typeof element !== 'object' || element === null) return [];
  return Object.entries(element as Record<string, unknown>).map(([key, value]) => ({
    key,
    value: Array.isArray(value) ? value.map(String).join(', ') : String(value),
  }));
}

function eventOf(x: EventXml): DomainEventView {
  return {
    event_id: x.event_id,
    kind: x.kind,
    outcome: x.outcome,
    occurred_at: x.occurred_at,
    recorded_at: x.recorded_at,
    actor_type: x.actor_type,
    actor_user_id: x.actor_user_id ?? null,
    actor_role: x.actor_role ?? null,
    source: x.source,
    store_id: x.store_id === undefined ? null : Number(x.store_id),
    subject_type: x.subject_type,
    subject_id: x.subject_id ?? null,
    model_version_id: x.model_version_id ?? null,
    rule_set_version: x.rule_set_version ?? null,
    correlation_id: x.correlation_id,
    details: detailsOf(x.details),
    prev_hash: x.prev_hash,
    entry_hash: x.entry_hash,
  };
}

/** A positive 64-bit integer, kept as text because it can exceed 2^53. */
function versionId(value: unknown, label: string): string | undefined {
  if (value === undefined || value === '') return undefined;
  if (typeof value !== 'string' || !/^[1-9][0-9]{0,18}$/.test(value) || BigInt(value) > MAX_AUDIT_ID)
    throw invalidInput(`${label} must be a whole number from 1 to 9223372036854775807.`);
  return value;
}

function matching(value: unknown, pattern: RegExp, label: string, rule: string): string | undefined {
  const text = optionalText(value, label)?.trim();
  if (text === undefined || text === '') return undefined;
  if (!pattern.test(text)) throw invalidInput(`${label} ${rule}.`);
  return text;
}

export class Audit {
  constructor(private readonly office: Backoffice) {}

  /** A role reads a call only when the contract's x-mosaiq-roles allow its operation. */
  private allowed(operation: string): Result<never> | null {
    const state = this.office.state();
    if (state.status !== 'signed_in')
      return { ok: false, error: localError('signed_out', 'You are signed out.') };
    return state.operations.includes(operation)
      ? null
      : { ok: false, error: localError('forbidden_role', 'Your role may not read the audit trail.') };
  }

  private run<T>(work: () => Promise<Result<T>>): Promise<Result<T>> {
    return checked(this.office, work);
  }

  listEntries(query: unknown): Promise<Result<Paged<AuditEntryView>>> {
    return this.run(async () => {
      const refused = this.allowed(AUDIT_OPERATIONS.entries);
      if (refused !== null) return refused;
      const q = record(query ?? {}, 'The search');
      const params = new URLSearchParams();
      const entity = oneOf(q.entity, AUDITED_ENTITIES, 'The entity');
      if (entity !== undefined) params.set('entity', entity);
      const pk = optionalText(q.entity_pk, 'The entity key')?.trim();
      if (pk) {
        if (pk.length > ENTITY_PK_MAX) throw invalidInput('The entity key is too long.');
        params.set('entity_pk', pk);
      }
      const user = optionalUuid(q.user_id, 'The user');
      if (user !== undefined) params.set('user_id', user);
      const action = oneOf(q.action, AUDIT_ACTIONS, 'The action');
      if (action !== undefined) params.set('action', action);
      const from = optionalDateTime(q.from, 'From');
      if (from !== undefined) params.set('from', from);
      const to = optionalDateTime(q.to, 'To');
      if (to !== undefined) params.set('to', to);
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.office.authed({ method: 'GET', path: `/v1/audit-entries?${params.toString()}` });
      if (!res.ok) return failed(res);
      const data = res.data?.AuditEntries as { AuditEntry?: EntryXml[] } & Record<string, unknown>;
      return { ok: true, value: { items: (data.AuditEntry ?? []).map(entryOf), page: pageOf(data) } };
    });
  }

  getEntry(id: unknown): Promise<Result<AuditEntryView>> {
    return this.run(async () => {
      const refused = this.allowed(AUDIT_OPERATIONS.entry);
      if (refused !== null) return refused;
      const aid = auditId(id);
      const res = await this.office.authed({ method: 'GET', path: `/v1/audit-entries/${aid}` });
      return this.entryResult(res);
    });
  }

  /** The domain events that match every filter given, newest recorded first (ADR-0072). */
  listEvents(query: unknown): Promise<Result<Paged<DomainEventView>>> {
    return this.run(async () => {
      const refused = this.allowed(AUDIT_OPERATIONS.events);
      if (refused !== null) return refused;
      const q = record(query ?? {}, 'The search');
      // The service's own order of filters: kind, actor, version, subject, store, outcome, source, dates.
      const params = new URLSearchParams();
      const kind = oneOf(q.kind, DOMAIN_EVENT_KINDS, 'The kind');
      if (kind !== undefined) params.set('kind', kind);
      const actor = optionalUuid(q.actor_user_id, 'The actor');
      if (actor !== undefined) params.set('actor_user_id', actor);
      const role = matching(q.actor_role, ROLE_CODE, 'The actor role', 'must be a role code such as AUDITOR');
      if (role !== undefined) params.set('actor_role', role);
      const version = versionId(q.model_version_id, 'The model version');
      if (version !== undefined) params.set('model_version_id', version);
      const subjectType = oneOf(q.subject_type, DOMAIN_EVENT_SUBJECT_TYPES, 'The subject type');
      if (subjectType !== undefined) params.set('subject_type', subjectType);
      const subjectId = optionalText(q.subject_id, 'The subject id')?.trim();
      if (subjectId) {
        if (subjectId.length > SUBJECT_ID_MAX) throw invalidInput('The subject id is too long.');
        params.set('subject_id', subjectId);
      }
      if (q.store_id !== undefined && q.store_id !== '')
        params.set('store_id', String(wholeNumber(q.store_id, MAX_STORE_ID, 'The store')));
      const outcome = oneOf(q.outcome, DOMAIN_EVENT_OUTCOMES, 'The outcome');
      if (outcome !== undefined) params.set('outcome', outcome);
      const source = matching(
        q.source,
        SOURCE_NAME,
        'The source',
        'must be a service role such as svc_models',
      );
      if (source !== undefined) params.set('source', source);
      const from = optionalDateTime(q.from, 'From');
      if (from !== undefined) params.set('from', from);
      const to = optionalDateTime(q.to, 'To');
      if (to !== undefined) params.set('to', to);
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.office.authed({ method: 'GET', path: `/v1/domain-events?${params.toString()}` });
      if (!res.ok) return failed(res);
      const data = res.data?.DomainEvents as { DomainEvent?: EventXml[] } & Record<string, unknown>;
      return { ok: true, value: { items: (data.DomainEvent ?? []).map(eventOf), page: pageOf(data) } };
    });
  }

  getEvent(eventId: unknown): Promise<Result<DomainEventView>> {
    return this.run(async () => {
      const refused = this.allowed(AUDIT_OPERATIONS.event);
      if (refused !== null) return refused;
      const eid = uuid(eventId, 'The event id');
      const res = await this.office.authed({ method: 'GET', path: `/v1/domain-events/${eid}` });
      if (!res.ok) return failed(res);
      return { ok: true, value: eventOf(res.data?.DomainEvent as EventXml) };
    });
  }

  private entryResult(res: CallResult): Result<AuditEntryView> {
    if (!res.ok) return failed(res);
    return { ok: true, value: entryOf(res.data?.AuditEntry as EntryXml) };
  }
}
