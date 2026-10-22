import type { AppState, ErrorView } from '../shared/api';

export interface RenderState {
  state: AppState;
  error?: ErrorView;
}

function sameSignedOutState(left: AppState, right: AppState): boolean {
  return (
    left.status === 'signed_out' &&
    right.status === 'signed_out' &&
    left.storage === right.storage &&
    left.notice === right.notice
  );
}

/** Keeps an action refusal when a duplicate state event arrives on the other IPC channel. */
export function reconcileRender(
  previous: RenderState | undefined,
  state: AppState,
  error?: ErrorView,
): RenderState {
  if (error !== undefined) return { state, error };
  if (previous?.error !== undefined && sameSignedOutState(previous.state, state)) {
    return { state, error: previous.error };
  }
  return { state };
}
