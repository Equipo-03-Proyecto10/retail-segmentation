// Types shared by the main process, the preload bridge and the screens.
// Nothing in here carries a credential.

export interface FieldDetail {
  field: string;
  code: string;
}

/** The common/v1 Error envelope as shown on screen. */
export interface ErrorView {
  code: string;
  message: string;
  correlation_id: string;
  details: FieldDetail[];
}

export interface ServiceView {
  name: string;
  resources: string[];
}

/** os_keyring: tokens sealed by the OS keyring and kept across restarts. memory_only: no keyring. */
export type Storage = 'os_keyring' | 'memory_only';

export type AppState =
  | { status: 'signed_out'; storage: Storage; notice?: string }
  | { status: 'signed_in'; storage: Storage; role: string; services: ServiceView[] };

export type ActionResult = { ok: true; state: AppState } | { ok: false; error: ErrorView; state: AppState };

export type ProbeResult =
  | { ok: true; service: string; status: number; root: string; correlation_id: string }
  | { ok: false; error: ErrorView };

/** The named operations the renderer may call; there is no generic transport. */
export interface MosaiqBridge {
  getState(): Promise<AppState>;
  signIn(credentials: { email: string; password: string }): Promise<ActionResult>;
  signOut(): Promise<ActionResult>;
  probeService(service: string): Promise<ProbeResult>;
  onStateChange(listener: (state: AppState) => void): () => void;
}

export const CHANNELS = {
  getState: 'mosaiq:get-state',
  signIn: 'mosaiq:sign-in',
  signOut: 'mosaiq:sign-out',
  probeService: 'mosaiq:probe-service',
  stateChanged: 'mosaiq:state-changed',
} as const;
