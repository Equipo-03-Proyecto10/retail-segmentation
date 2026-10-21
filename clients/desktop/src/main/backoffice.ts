import type { ActionResult, AppState, ErrorView, ProbeResult } from '../shared/api';
import { servicesForRole, type Bundle } from './bundle';
import { ContractDefect, defectView, type CallOptions, type CallResult, type Transport } from './transport';

export interface Tokens {
  access: string;
  refresh: string;
  /** Epoch milliseconds. */
  expiresAt: number;
}

/** Where tokens rest. The real implementation encrypts with safeStorage. */
export interface TokenVault {
  load(): Tokens | null;
  save(tokens: Tokens): void;
  clear(): void;
}

const TERMINAL_REFRESH: Record<string, string> = {
  refresh_reused: 'Your session was closed because its refresh credential was reused. Sign in again.',
  invalid_refresh: 'Your session is no longer valid. Sign in again.',
  session_expired: 'Your session has expired. Sign in again.',
  session_revoked: 'Your session was revoked. Sign in again.',
};

const PLAIN_MESSAGE: Record<string, string> = {
  auth_unavailable: 'Authentication is unavailable',
  database_unavailable: 'The service is unavailable, try again',
};

const STAFF_ONLY = 'The back office is for staff roles. Customer accounts cannot sign in here.';
const REFRESH_SKEW_MS = 30_000;

function plain(error: ErrorView): ErrorView {
  const message = PLAIN_MESSAGE[error.code];
  return message === undefined ? error : { ...error, message };
}

function localError(code: string, message: string): ErrorView {
  return { code, message, correlation_id: '-', details: [] };
}

/** The session was ended; the renderer must show the sign-in screen. */
class SessionEnded extends Error {
  constructor(readonly view: ErrorView) {
    super(view.code);
  }
}

/** A call that failed without ending the session (for example a 503). */
class CallFailed extends Error {
  constructor(readonly view: ErrorView) {
    super(view.code);
  }
}

export class Backoffice {
  private tokens: Tokens | null;
  private role: string | null = null;
  private notice: string | undefined;
  private refreshing: Promise<void> | undefined;
  private listeners = new Set<(state: AppState) => void>();

  constructor(
    private readonly bundle: Bundle,
    private readonly transport: Transport,
    private readonly vault: TokenVault,
    private readonly deviceLabel: string,
    private readonly now: () => number = Date.now,
  ) {
    this.tokens = vault.load();
  }

  onStateChange(listener: (state: AppState) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  state(): AppState {
    if (this.tokens === null || this.role === null) return { status: 'signed_out', notice: this.notice };
    return { status: 'signed_in', role: this.role, services: servicesForRole(this.bundle, this.role) };
  }

  private publish(): void {
    const state = this.state();
    for (const l of this.listeners) l(state);
  }

  private endSession(notice: string): void {
    this.tokens = null;
    this.role = null;
    this.notice = notice;
    this.vault.clear();
    this.publish();
  }

  /** Resumes a stored session: resolves the role through the auth service. */
  async restore(): Promise<AppState> {
    if (this.tokens === null) return this.state();
    try {
      await this.loadRole();
    } catch (e) {
      if (!(e instanceof SessionEnded)) this.notice = 'The service is unavailable, try again';
    }
    return this.state();
  }

  private async loadRole(): Promise<void> {
    const res = await this.authed({ method: 'GET', path: '/v1/sessions/current' });
    if (!res.ok) throw new CallFailed(plain(res.error));
    const claims = res.data?.SessionClaims as { role: string };
    if (claims.role === 'CUSTOMER') {
      await this.signOutRemote();
      this.endSession(STAFF_ONLY);
      return;
    }
    this.role = claims.role;
    this.notice = undefined;
    this.publish();
  }

  async signIn(credentials: { email: string; password: string }): Promise<ActionResult> {
    try {
      const res = await this.transport.call({
        method: 'POST',
        path: '/v1/sessions',
        body: {
          root: 'SignInRequest',
          fields: {
            email: credentials.email,
            password: credentials.password,
            device_label: this.deviceLabel,
          },
        },
      });
      if (!res.ok) return { ok: false, error: plain(res.error), state: this.state() };
      this.tokens = tokensFrom(res, this.now());
      this.vault.save(this.tokens);
      try {
        await this.loadRole();
      } catch (e) {
        if (e instanceof SessionEnded) return { ok: false, error: e.view, state: this.state() };
        this.tokens = null;
        this.vault.clear();
        throw e;
      }
      if (this.role === null)
        return { ok: false, error: localError('staff_only', STAFF_ONLY), state: this.state() };
      return { ok: true, state: this.state() };
    } catch (e) {
      return { ok: false, error: this.viewOf(e), state: this.state() };
    }
  }

  async signOut(): Promise<ActionResult> {
    await this.signOutRemote();
    this.endSession('You have signed out.');
    return { ok: true, state: this.state() };
  }

  private async signOutRemote(): Promise<void> {
    if (this.tokens === null) return;
    try {
      await this.transport.call({
        method: 'DELETE',
        path: '/v1/sessions/current',
        bearer: this.tokens.access,
      });
    } catch {
      // The tokens are erased either way; the session ends on its own lifetime.
    }
  }

  /** Reads the first collection operation of a service, as a connectivity and contract check. */
  async probeService(service: string): Promise<ProbeResult> {
    const op = this.bundle.operations.find(
      (o) =>
        o.service === service && o.method === 'GET' && !o.path.includes('{') && o.path.startsWith('/v1/'),
    );
    if (op === undefined)
      return { ok: false, error: localError('no_probe', `No readable collection in ${service}.`) };
    try {
      const res = await this.authed({ method: 'GET', path: op.path });
      if (!res.ok) return { ok: false, error: plain(res.error) };
      return {
        ok: true,
        service,
        status: res.status,
        root: res.root ?? '',
        correlation_id: res.correlationId,
      };
    } catch (e) {
      return { ok: false, error: this.viewOf(e) };
    }
  }

  private viewOf(e: unknown): ErrorView {
    if (e instanceof SessionEnded || e instanceof CallFailed) return e.view;
    if (e instanceof ContractDefect) return defectView(e);
    if (e instanceof TypeError) return localError('network_unreachable', 'The service could not be reached.');
    return localError('client_error', 'The request could not be completed.');
  }

  /** An authenticated call: refreshes ahead of expiry and retries once after invalid_token. */
  async authed(options: Omit<CallOptions, 'bearer'>): Promise<CallResult> {
    if (this.tokens === null) throw new SessionEnded(localError('signed_out', 'You are signed out.'));
    if (this.tokens.expiresAt - this.now() < REFRESH_SKEW_MS) await this.refresh(this.tokens.access);
    const used = this.tokens as Tokens;
    const first = await this.transport.call({ ...options, bearer: used.access });
    if (first.ok || first.status !== 401 || first.error.code !== 'invalid_token') return first;
    await this.refresh(used.access);
    return this.transport.call({ ...options, bearer: (this.tokens as Tokens).access });
  }

  /** Single-flight: one refresh serves every waiting call. */
  private refresh(stale: string): Promise<void> {
    if (this.tokens === null)
      return Promise.reject(new SessionEnded(localError('signed_out', 'You are signed out.')));
    if (this.tokens.access !== stale) return Promise.resolve(); // already rotated
    this.refreshing ??= this.doRefresh().finally(() => {
      this.refreshing = undefined;
    });
    return this.refreshing;
  }

  private async doRefresh(): Promise<void> {
    const current = this.tokens as Tokens;
    const res = await this.transport.call({
      method: 'POST',
      path: '/v1/sessions/refresh',
      body: { root: 'RefreshRequest', fields: { refresh_token: current.refresh } },
    });
    if (res.ok) {
      this.tokens = tokensFrom(res, this.now());
      this.vault.save(this.tokens);
      return;
    }
    const reason = TERMINAL_REFRESH[res.error.code];
    if (reason !== undefined && res.status === 401) {
      this.endSession(reason);
      throw new SessionEnded({ ...res.error, message: reason });
    }
    throw new CallFailed(plain(res.error));
  }
}

function tokensFrom(res: CallResult, now: number): Tokens {
  if (!res.ok || res.data === undefined) throw new Error('token result expected');
  const t = res.data.TokenResult as { access_token: string; refresh_token: string; expires_at: string };
  const at = Date.parse(t.expires_at);
  return {
    access: t.access_token,
    refresh: t.refresh_token,
    expiresAt: Number.isNaN(at) ? now + 600_000 : at,
  };
}
