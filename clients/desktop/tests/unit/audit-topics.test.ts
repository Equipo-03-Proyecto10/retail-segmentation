import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  AUDITED_ENTITIES,
  DOMAIN_EVENT_KINDS,
  DOMAIN_EVENT_SUBJECT_TYPES,
  type DomainEventView,
} from '../../src/shared/api';
import { AUDIT_TOPICS } from '../../src/shared/audit-topics';
import { AUDIT_OPERATIONS, visibleGroups } from '../../src/shared/modules';
import { bundle } from '../support/fake';

// The four shortcuts of the audit module (F28-15, #884) against the contract they filter.

const CONTRACTS = new URL('../../../../contracts/audit-trail', import.meta.url).pathname;
const page = JSON.parse(readFileSync(join(CONTRACTS, 'examples', 'domain-events-page.json'), 'utf8')) as {
  domain_events: (DomainEventView & { details: Record<string, unknown> })[];
};

describe('audit topics', () => {
  it('cover the four questions of the story, each a shortcut to filters the screen shows', () => {
    expect(AUDIT_TOPICS.map((t) => t.label)).toEqual([
      'Model versions',
      'Rule changes',
      'Campaign decisions',
      'Permissions',
    ]);
  });

  it('set only values the contract lists', () => {
    for (const t of AUDIT_TOPICS) {
      const { kind, subject_type: subject, entity } = t.preset;
      if (kind !== undefined) expect(DOMAIN_EVENT_KINDS as readonly string[]).toContain(kind);
      if (subject !== undefined) expect(DOMAIN_EVENT_SUBJECT_TYPES as readonly string[]).toContain(subject);
      if (entity !== undefined) expect(AUDITED_ENTITIES as readonly string[]).toContain(entity);
      // The list that answers a topic is the one its preset belongs to.
      expect(t.source === 'rows' ? entity !== undefined : kind !== undefined || subject !== undefined).toBe(
        true,
      );
    }
  });

  it('are offered by operations only ADMIN and AUDITOR may call', () => {
    const roles = new Map(bundle.operations.map((o) => [o.operationId, o.roles]));
    for (const t of AUDIT_TOPICS) expect(roles.get(t.operation), t.id).toEqual(['ADMIN', 'AUDITOR']);
    expect(Object.values(AUDIT_OPERATIONS).every((op) => roles.has(op))).toBe(true);
  });

  it('find the recorded decisions of the contract example, and nothing else', () => {
    const find = (id: string): string[] => {
      const topic = AUDIT_TOPICS.find((t) => t.id === id)!;
      return page.domain_events
        .filter(
          (e) =>
            (topic.preset.kind === undefined || e.kind === topic.preset.kind) &&
            (topic.preset.subject_type === undefined || e.subject_type === topic.preset.subject_type),
        )
        .map((e) => e.kind)
        .reverse();
    };
    expect(find('model-versions')).toEqual(['MODEL_PUBLISHED', 'MODEL_ROLLED_BACK']);
    expect(find('campaign-decisions')).toEqual(['CRITICAL_ACTION']);
    expect(find('permissions')).toEqual(['PERMISSION_CHANGED', 'PERMISSION_CHANGED']);
  });
});

describe('the audit module in the menu', () => {
  it('is offered with the row list of the trail, and a role without it sees nothing of it', () => {
    const names = (ops: string[]): string[] =>
      visibleGroups(ops).flatMap((g) => g.modules.map((m) => m.name));
    expect(names([AUDIT_OPERATIONS.entries])).toEqual(['audit', 'services']);
    expect(names(['listCampaigns'])).toEqual(['campaigns', 'services']);
    // Nothing but the audit operations of ADMIN and AUDITOR opens it.
    const allowed = (role: string): string[] =>
      bundle.operations.filter((o) => o.roles?.includes(role)).map((o) => o.operationId);
    expect(names(allowed('AUDITOR'))).toContain('audit');
    expect(names(allowed('ADMIN'))).toContain('audit');
    for (const role of ['ANALYST', 'MARKETING', 'STORE_MANAGER', 'INVENTORY_PLANNER'])
      expect(names(allowed(role)), role).not.toContain('audit');
  });
});
