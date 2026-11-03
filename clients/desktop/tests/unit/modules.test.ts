import { describe, expect, it } from 'vitest';
import { endsSession, MODULE_GROUPS, mayExport, visibleGroups } from '../../src/shared/modules';
import { guardBridge } from '../../src/renderer/session';
import type { MosaiqBridge } from '../../src/shared/api';

describe('module groups', () => {
  it('places every module in exactly one group', () => {
    const names = MODULE_GROUPS.flatMap((g) => g.modules.map((m) => m.name));
    expect(new Set(names).size).toBe(13);
    expect(names).toHaveLength(13);
  });

  it('shows a role only the modules whose operation it may call, and no empty group', () => {
    const groups = visibleGroups(['listCampaigns', 'listAuditEntries']);
    expect(groups.map((g) => [g.id, g.modules.map((m) => m.name)])).toEqual([
      ['marketing', ['campaigns']],
      ['administration', ['audit', 'services']],
    ]);
  });

  it('lets only the staff roles that analyse or audit export', () => {
    expect(['ADMIN', 'ANALYST', 'MARKETING', 'AUDITOR'].every(mayExport)).toBe(true);
    expect(mayExport('STORE_MANAGER')).toBe(false);
    expect(mayExport('CUSTOMER')).toBe(false);
  });

  it('treats the five session codes as the end of a session', () => {
    expect(endsSession('session_revoked')).toBe(true);
    expect(endsSession('forbidden_role')).toBe(false);
  });
});

describe('guardBridge', () => {
  const fake = (result: unknown): MosaiqBridge =>
    ({
      getState: () => Promise.resolve({}),
      listUsers: () => Promise.resolve(result),
    }) as unknown as MosaiqBridge;

  it('reports busy while a call is in flight and idle after', async () => {
    const seen: boolean[] = [];
    const bridge = guardBridge(fake({ ok: true, value: 1 }), {
      onBusy: (b) => seen.push(b),
      onSessionEnded: () => undefined,
    });
    await bridge.listUsers({} as never);
    expect(seen).toEqual([true, false]);
  });

  it('sends the user to sign-in and never hands the refusal to the module', async () => {
    let ended = 0;
    const bridge = guardBridge(fake({ ok: false, error: { code: 'session_expired' } }), {
      onBusy: () => undefined,
      onSessionEnded: () => (ended += 1),
    });
    const outcome = await Promise.race([
      bridge.listUsers({} as never).then(() => 'settled'),
      new Promise((r) => setTimeout(() => r('pending'), 20)),
    ]);
    expect(outcome).toBe('pending');
    expect(ended).toBe(1);
  });

  it('passes an ordinary refusal through', async () => {
    const refusal = { ok: false, error: { code: 'forbidden_role' } };
    const bridge = guardBridge(fake(refusal), { onBusy: () => undefined, onSessionEnded: () => undefined });
    expect(await bridge.listUsers({} as never)).toBe(refusal);
  });
});
