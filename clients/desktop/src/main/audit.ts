import { AUDIT_ACTIONS, AUDITED_ENTITIES, type AuditEntryView, type Paged, type Result } from '../shared/api';
import type { Backoffice } from './backoffice';
import {
  checked,
  failed,
  invalidInput,
  optionalDateTime,
  optionalText,
  optionalUuid,
  pageNumber,
  pageOf,
  pageSize,
  record,
} from './checks';
import type { CallResult } from './transport';

// The audit trail (F16-09, #487): read only, through audit-trail. Every filter
// is checked here before a query string is built. The trail has no write
// endpoint, so this class has no write method.

const ENTITY_PK_MAX = 80;
const MAX_AUDIT_ID = 9223372036854775807n;

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

export class Audit {
  constructor(private readonly office: Backoffice) {}

  private run<T>(work: () => Promise<Result<T>>): Promise<Result<T>> {
    return checked(this.office, work);
  }

  listEntries(query: unknown): Promise<Result<Paged<AuditEntryView>>> {
    return this.run(async () => {
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
      const aid = auditId(id);
      const res = await this.office.authed({ method: 'GET', path: `/v1/audit-entries/${aid}` });
      return this.entryResult(res);
    });
  }

  private entryResult(res: CallResult): Result<AuditEntryView> {
    if (!res.ok) return failed(res);
    return { ok: true, value: entryOf(res.data?.AuditEntry as EntryXml) };
  }
}
