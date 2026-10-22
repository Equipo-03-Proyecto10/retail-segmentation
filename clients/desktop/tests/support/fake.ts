import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { loadBundle } from '../../src/main/bundle';
import { Backoffice, type Protection, type SealedTokens, type TokenVault } from '../../src/main/backoffice';
import { MemorySealer } from '../../src/main/sealer';
import { Transport } from '../../src/main/transport';
import { XsdValidator } from '../../src/main/xml';
export { deferred, type Deferred } from './deferred';

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
export interface Reply {
  status: number;
  body?: string;
  headers?: Record<string, string>;
}
export type Responder = (req: Seen) => Reply | Promise<Reply>;

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
    return new Response(r.body ?? null, {
      status: r.status,
      headers: { 'Content-Type': 'application/xml', ...r.headers },
    });
  };
  return { fetch: impl as typeof fetch, seen };
}

export interface Tokens {
  access: string;
  refresh: string;
  expiresAt: number;
}

/** A vault that seals like the memory_only mode and lets a test read what it holds. */
export class MemoryVault implements TokenVault {
  readonly protection: Protection;
  private readonly sealer = new MemorySealer();
  sealed: SealedTokens | null = null;

  constructor(tokens: Tokens | null = null, protection: Protection = 'os_keyring') {
    this.protection = protection;
    if (tokens !== null)
      this.sealed = {
        access: this.seal(tokens.access),
        refresh: this.seal(tokens.refresh),
        expiresAt: tokens.expiresAt,
      };
  }
  /** What the vault holds, opened, for assertions. */
  get tokens(): Tokens | null {
    const s = this.sealed;
    return s === null
      ? null
      : { access: this.open(s.access), refresh: this.open(s.refresh), expiresAt: s.expiresAt };
  }
  seal(plain: string): Buffer {
    return this.sealer.seal(plain);
  }
  open(sealed: Buffer): string {
    return this.sealer.open(sealed);
  }
  load(): SealedTokens | null {
    return this.sealed;
  }
  save(t: SealedTokens): void {
    this.sealed = t;
  }
  clear(): void {
    this.sealed = null;
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

const CONTRACTS = new URL('../../../../contracts', import.meta.url).pathname;

/** A recorded example from contracts/<service>/examples/, read from disk so tests never retype it. */
export function example(service: string, name: string): string {
  return readFileSync(join(CONTRACTS, service, 'examples', `${name}.xml`), 'utf8');
}
