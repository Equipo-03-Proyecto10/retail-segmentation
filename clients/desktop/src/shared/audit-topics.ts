import { AUDIT_OPERATIONS } from './modules';

// The four questions an auditor brings to the audit module (F28-15, #884): who changed a model
// version, a rule, a campaign decision or a permission, and when. Each is only a shortcut that
// fills in filters, which stay visible and editable on the screen, so what is sent is what is shown.
// Pure data, so a unit test can pin it against the contract.

export interface AuditTopicPreset {
  /** A domain-event kind. */
  kind?: string;
  /** What a domain event is about. */
  subject_type?: string;
  /** An audited table, for the row changes of audit-entries. */
  entity?: string;
}

export interface AuditTopic {
  id: 'model-versions' | 'rule-changes' | 'campaign-decisions' | 'permissions';
  label: string;
  /** The list that answers it: the domain events (ADR-0072), or the row changes of audit-entries (RN-28). */
  source: 'events' | 'rows';
  preset: AuditTopicPreset;
  /** The operation a role must be allowed to call to be offered the shortcut. */
  operation: string;
}

export const AUDIT_TOPICS: readonly AuditTopic[] = [
  {
    // MODEL_PUBLISHED and MODEL_ROLLED_BACK are the only kinds about a model version, and a kind
    // filter takes one value, so the subject type finds both.
    id: 'model-versions',
    label: 'Model versions',
    source: 'events',
    preset: { subject_type: 'MODEL_VERSION' },
    operation: AUDIT_OPERATIONS.events,
  },
  {
    // ADR-0072 has no event kind for editing a rule: a rule is a row, and RN-28 records every
    // change to one in audit-entries.
    id: 'rule-changes',
    label: 'Rule changes',
    source: 'rows',
    preset: { entity: 'segment_rule' },
    operation: AUDIT_OPERATIONS.entries,
  },
  {
    id: 'campaign-decisions',
    label: 'Campaign decisions',
    source: 'events',
    preset: { kind: 'CRITICAL_ACTION', subject_type: 'CAMPAIGN' },
    operation: AUDIT_OPERATIONS.events,
  },
  {
    id: 'permissions',
    label: 'Permissions',
    source: 'events',
    preset: { kind: 'PERMISSION_CHANGED' },
    operation: AUDIT_OPERATIONS.events,
  },
];
