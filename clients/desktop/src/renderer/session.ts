import type { MosaiqBridge } from '../shared/api';
import { endsSession } from '../shared/modules';

// The shell's two guards over the bridge every module shares (F28-01, #870): one loading
// indicator for any call in flight, and one way back to sign-in when the session is over.

const PASS_THROUGH = new Set(['onStateChange', 'getState', 'signIn', 'signOut']);

export interface Guards {
  /** Called with true when the first call starts and false when the last one settles. */
  onBusy(busy: boolean): void;
  /** Called when a call was refused because the session is over; the shell shows sign-in. */
  onSessionEnded(): void;
}

function sessionError(value: unknown): boolean {
  if (typeof value !== 'object' || value === null) return false;
  const r = value as { ok?: unknown; error?: { code?: unknown } };
  return r.ok === false && typeof r.error?.code === 'string' && endsSession(r.error.code);
}

export function guardBridge(bridge: MosaiqBridge, guards: Guards): MosaiqBridge {
  let inFlight = 0;
  const settle = (): void => {
    inFlight -= 1;
    if (inFlight === 0) guards.onBusy(false);
  };
  const wrapped: Record<string, unknown> = {};
  for (const [name, member] of Object.entries(bridge) as [string, unknown][]) {
    if (typeof member !== 'function' || PASS_THROUGH.has(name)) {
      wrapped[name] = member;
      continue;
    }
    wrapped[name] = (...args: unknown[]): Promise<unknown> => {
      if (inFlight === 0) guards.onBusy(true);
      inFlight += 1;
      return (member as (...a: unknown[]) => Promise<unknown>)(...args).then(
        (value) => {
          settle();
          if (!sessionError(value)) return value;
          guards.onSessionEnded();
          // The module that asked is about to be replaced by sign-in. It never receives the
          // refusal, so it cannot paint an error or a half-built screen over it.
          return new Promise<never>(() => undefined);
        },
        (error: unknown) => {
          settle();
          throw error;
        },
      );
    };
  }
  return wrapped as unknown as MosaiqBridge;
}
