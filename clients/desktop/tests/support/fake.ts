import { loadBundle } from '../../src/main/bundle';
import { Backoffice, type TokenVault, type Tokens } from '../../src/main/backoffice';
import { Transport } from '../../src/main/transport';
import { XsdValidator } from '../../src/main/xml';

export const AUTH_NS = 'https://mosaiq.maxthecoder.online/xml/auth/v1';
export const COMMON_NS = 'https://mosaiq.maxthecoder.online/xml/common/v1';
export const bundle = loadBundle(new URL('../../bundled', import.meta.url).pathname);

export const tokenResult = (access: string, refresh: string, expiresAt = '2099-01-01T00:00:00Z'): string =>
  `<?xml version="1.0"?><TokenResult xmlns="${AUTH_NS}"><access_token>${access}</access_token><refresh_token>${refresh}</refresh_token><token_type>Bearer</token_type><expires_in>600</expires_in><expires_at>${expiresAt}</expires_at></TokenResult>`;

export const claims = (role: string): string =>
  `<SessionClaims xmlns="${AUTH_NS}"><sub>5b0c2f3e-8a41-4d6b-9c10-2e7a1f4d9b33</sub><sid>83d412c4-e4ab-45f7-9ef1-576cd4c32b89</sid><role>${role}</role><issued_at>2026-10-15T08:10:00Z</issued_at><expires_at>2026-10-15T08:20:00Z</expires_at></SessionClaims>`;

export const errorXml = (code: string, message = 'm', details = ''): string =>
  `<Error xmlns="${COMMON_NS}"><code>${code}</code><message>${message}</message><correlation_id>srv-1</correlation_id>${details}</Error>`;

export interface Seen {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: string | undefined;
}
export type Responder = (
  req: Seen,
) => { status: number; body?: string } | Promise<{ status: number; body?: string }>;

export function fakeFetch(responder: Responder): { fetch: typeof fetch; seen: Seen[] } {
  const seen: Seen[] = [];
  const impl = async (url: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const req: Seen = {
      method: init?.method ?? 'GET',
      url: String(url),
      headers: (init?.headers ?? {}) as Record<string, string>,
      body: init?.body as string | undefined,
    };
    seen.push(req);
    const r = await responder(req);
    return new Response(r.body ?? null, { status: r.status, headers: { 'Content-Type': 'application/xml' } });
  };
  return { fetch: impl as typeof fetch, seen };
}

export class MemoryVault implements TokenVault {
  constructor(public tokens: Tokens | null = null) {}
  load(): Tokens | null {
    return this.tokens;
  }
  save(t: Tokens): void {
    this.tokens = t;
  }
  clear(): void {
    this.tokens = null;
  }
}

export function makeTransport(responder: Responder): { transport: Transport; seen: Seen[] } {
  const f = fakeFetch(responder);
  return {
    transport: new Transport(bundle, new XsdValidator(bundle), () => 'http://gw.test', f.fetch as never),
    seen: f.seen,
  };
}

export function makeOffice(
  responder: Responder,
  vault = new MemoryVault(),
  now: () => number = Date.now,
): { office: Backoffice; seen: Seen[]; vault: MemoryVault } {
  const { transport, seen } = makeTransport(responder);
  return { office: new Backoffice(bundle, transport, vault, 'test device', now), seen, vault };
}
