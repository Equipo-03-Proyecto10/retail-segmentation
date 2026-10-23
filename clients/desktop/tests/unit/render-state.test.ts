import { describe, expect, it } from 'vitest';
import type { AppState, ErrorView } from '../../src/shared/api';
import { reconcileRender } from '../../src/renderer/render-state';

const signedOut: AppState = {
  status: 'signed_out',
  storage: 'os_keyring',
  notice: 'The back office is for staff roles. Customer accounts cannot sign in here.',
};

const refusal: ErrorView = {
  code: 'staff_only',
  message: 'The back office is for staff roles. Customer accounts cannot sign in here.',
  correlation_id: '-',
  details: [],
};

describe('renderer state reconciliation', () => {
  it('keeps a sign-in refusal when its duplicate state event arrives after the reply', () => {
    const afterReply = reconcileRender(undefined, signedOut, refusal);
    const afterLateEvent = reconcileRender(afterReply, { ...signedOut });

    expect(afterLateEvent.error).toEqual(refusal);
  });

  it('clears an old refusal when the signed-out state actually changes', () => {
    const afterReply = reconcileRender(undefined, signedOut, refusal);
    const next = reconcileRender(afterReply, { ...signedOut, notice: 'You have signed out.' });

    expect(next.error).toBeUndefined();
  });
});
