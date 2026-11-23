import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type { CampaignView, ErrorView, SaleView } from '../src/shared/api';
import { Backoffice, type SealedTokens, type TokenVault } from '../src/main/backoffice';
import { operationsForRole, type Bundle } from '../src/main/bundle';
import { Campaigns } from '../src/main/campaigns';
import { Floor } from '../src/main/floor';
import { MemorySealer } from '../src/main/sealer';
import { Transport, type FetchLike } from '../src/main/transport';
import { XsdValidator } from '../src/main/xml';

// F20-02 (#760): the desktop client's own calls, recorded for the evidence
// (docs/evidence/f20-02-electron-sale-campaign.md is the runbook).
//
// Every request is built, sent and checked by the main-process code the window
// uses: Transport (XML only, each request and each response validated against
// the bundled XSD), Backoffice (sign-in, the sealed tokens, sign-out), Floor.getSale
// and Campaigns.createCampaign. Only two things are this script's own: a vault
// that keeps the sealed tokens in memory (the app's memory_only mode), and a fetch
// that records each exchange as it passes. Credentials and tokens are masked
// before anything is written, and nothing is written if a token survives.

export const STEPS = ['staff', 'marketing', 'forbidden'] as const;
export type Step = (typeof STEPS)[number];

export interface Account {
  email: string;
  password: string;
}

export interface CampaignDraft {
  name: string;
  label_code: string;
  starts_on: string;
  ends_on: string;
}

export interface Options {
  bundle: Bundle;
  baseUrl: string;
  out: string;
  saleId: number;
  draft: CampaignDraft;
  accounts: Record<Step, Account>;
  fetchImpl?: FetchLike;
  /** Written into result.json as they are; the CLI fills them from package.json and git. */
  build?: Record<string, string>;
}

export interface Recorded {
  number: number;
  step: Step;
  correlation_id: string;
  method: string;
  url: string;
  request_headers: Record<string, string>;
  request_body: string;
  status: number;
  response_headers: Record<string, string>;
  response_body: string;
  at: string;
  /** Files under bodies/ holding the exact bytes, for xmllint; never for sign-in or refresh. */
  request_file?: string;
  response_file?: string;
}

export interface StepResult {
  account: string;
  role: string | null;
  signed_in: boolean;
  /** Whether the role may call the operation, per the bundled x-mosaiq-roles (what the screen draws). */
  may_call: boolean;
  ok: boolean;
  status?: number;
  correlation_id?: string;
  error?: ErrorView;
  sale?: SaleView;
  campaign?: CampaignView;
  /** The exchange the operation's main request was recorded as. */
  exchange?: number;
}

export interface Evidence {
  started_at: string;
  finished_at: string;
  base_url: string;
  sale_id: number;
  draft: CampaignDraft;
  build: Record<string, string>;
  steps: Record<Step, StepResult>;
}

const JWT = /eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+/g;
const SECRET_ELEMENTS = ['password', 'current_password', 'new_password', 'access_token', 'refresh_token'];
const KEPT_RESPONSE_HEADERS = ['content-type', 'x-correlation-id', 'location', 'retry-after'];
const SECRET_HEADER = /^(cookie|set-cookie|x-api-key|x-auth-token|x-access-token)$/;
const UNRECORDED_BODIES = /\/api\/auth\/v1\/sessions(\/refresh)?$/;

export class UnmaskedSecret extends Error {}

/** Each distinct secret becomes `<masked #n>`, as tests/acceptance/evidence.py marks them. */
export class Masker {
  private readonly seen = new Map<string, number>();
  private readonly known = new Set<string>();

  mark(secret: string): string {
    this.known.add(secret);
    let n = this.seen.get(secret);
    if (n === undefined) {
      n = this.seen.size + 1;
      this.seen.set(secret, n);
    }
    return `<masked #${n}>`;
  }

  /** A secret that must never reach a file, even one the masking rules would not find (a password). */
  watch(secret: string): void {
    if (secret !== '') this.known.add(secret);
  }

  /** Whether the text holds a token-shaped string or any secret this run has seen or watches. */
  leaks(text: string): boolean {
    JWT.lastIndex = 0;
    if (JWT.test(text)) return true;
    for (const secret of this.known) if (text.includes(secret)) return true;
    return false;
  }

  header(name: string, value: string): string {
    const lower = name.toLowerCase();
    if (lower === 'authorization' || lower === 'proxy-authorization') {
      const [scheme, ...rest] = value.split(' ');
      return rest.length > 0 ? `${scheme} ${this.mark(rest.join(' '))}` : this.mark(value);
    }
    if (SECRET_HEADER.test(lower)) return this.mark(value);
    return value;
  }

  body(text: string): string {
    let masked = text;
    for (const element of SECRET_ELEMENTS) {
      const xml = new RegExp(`(<(?:[\\w-]+:)?${element}>)([^<]*)(</(?:[\\w-]+:)?${element}>)`, 'g');
      masked = masked.replace(xml, (_m, open: string, secret: string, close: string) =>
        secret === '' ? `${open}${close}` : `${open}${this.mark(secret)}${close}`,
      );
      // A JSON answer (a proxy's error page, say) names the same fields as members.
      const json = new RegExp(`("${element}"\\s*:\\s*")((?:[^"\\\\]|\\\\.)+)(")`, 'g');
      masked = masked.replace(
        json,
        (_m, open: string, secret: string, close: string) => `${open}${this.mark(secret)}${close}`,
      );
    }
    return masked.replace(JWT, (m) => this.mark(m));
  }
}

/** The app's memory_only mode: sealed under a key made at start-up, nothing written. */
class MemoryOnlyVault implements TokenVault {
  readonly protection = 'memory_only' as const;
  private readonly sealer = new MemorySealer();
  seal(plain: string): Buffer {
    return this.sealer.seal(plain);
  }
  open(sealed: Buffer): string {
    return this.sealer.open(sealed);
  }
  load(): SealedTokens | null {
    return null;
  }
  save(): void {}
  clear(): void {}
}

function entriesOf(headers: Headers): Record<string, string> {
  const found: Record<string, string> = {};
  headers.forEach((value, name) => {
    found[name.toLowerCase()] = value;
  });
  return found;
}

function headersOf(init: RequestInit): Record<string, string> {
  const h = init.headers;
  if (h === undefined) return {};
  if (h instanceof Headers) return entriesOf(h);
  if (Array.isArray(h)) return Object.fromEntries(h);
  return { ...(h as Record<string, string>) };
}

class Recorder {
  readonly exchanges: Recorded[] = [];
  /** The exact bytes kept for xmllint, by path under the output directory; written only once all is checked. */
  readonly bodies = new Map<string, string>();
  readonly masker = new Masker();
  step: Step = 'staff';

  constructor(private readonly inner: FetchLike) {}

  /** A fetch for Transport: sends through the real fetch and records what passed. */
  readonly fetch: FetchLike = async (url, init) => {
    const response = await this.inner(url, init);
    const text = await response.text();
    const sent = headersOf(init);
    const number = this.exchanges.length + 1;
    const path = new URL(url).pathname;
    const requestBody = typeof init.body === 'string' ? init.body : '';
    const entry: Recorded = {
      number,
      step: this.step,
      correlation_id: sent['X-Correlation-ID'] ?? '',
      method: init.method ?? 'GET',
      url,
      request_headers: Object.fromEntries(
        Object.entries(sent).map(([k, v]) => [k, this.masker.header(k, v)]),
      ),
      request_body: this.masker.body(requestBody),
      status: response.status,
      response_headers: Object.fromEntries(
        Object.entries(entriesOf(response.headers)).filter(([k]) => KEPT_RESPONSE_HEADERS.includes(k)),
      ),
      response_body: this.masker.body(text),
      at: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
    };
    // The exact bytes, for an xmllint run that does not trust this client; never a token's.
    if (!UNRECORDED_BODIES.test(path)) {
      const stem = `${String(number).padStart(3, '0')}-${this.step}`;
      if (requestBody !== '') {
        entry.request_file = `bodies/${stem}-request.xml`;
        this.bodies.set(entry.request_file, requestBody);
      }
      if (text !== '') {
        entry.response_file = `bodies/${stem}-response.xml`;
        this.bodies.set(entry.response_file, text);
      }
    }
    this.exchanges.push(entry);
    return new Response(text === '' ? null : text, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  };

  last(method: string, pathEnd: string): Recorded | undefined {
    return [...this.exchanges]
      .reverse()
      .find((x) => x.step === this.step && x.method === method && new URL(x.url).pathname.endsWith(pathEnd));
  }
}

function markdown(evidence: Evidence, exchanges: Recorded[]): string {
  const lines = ["# F20-02: the desktop client's requests and responses", ''];
  lines.push(`Base URL \`${evidence.base_url}\`, ${evidence.started_at} – ${evidence.finished_at}.`, '');
  for (const x of exchanges) {
    lines.push(`<a id="exchange-${x.number}"></a>`);
    lines.push(`## ${x.number}. ${x.method} ${new URL(x.url).pathname} (${x.step})`, '');
    lines.push(`\`X-Correlation-ID: ${x.correlation_id}\`, at ${x.at}`, '');
    lines.push('```http', `${x.method} ${x.url}`);
    for (const [k, v] of Object.entries(x.request_headers)) lines.push(`${k}: ${v}`);
    lines.push('', x.request_body, '```', '', '```http', `HTTP ${x.status}`);
    for (const [k, v] of Object.entries(x.response_headers)) lines.push(`${k}: ${v}`);
    lines.push('', x.response_body, '```', '');
  }
  return lines.join('\n');
}

/** Runs the three steps, writes result.json, exchanges.json, exchanges.md and bodies/. */
export async function collect(options: Options): Promise<Evidence> {
  const recorder = new Recorder(options.fetchImpl ?? ((url, init) => fetch(url, init)));
  for (const account of Object.values(options.accounts)) recorder.masker.watch(account.password);
  const validator = new XsdValidator(options.bundle);
  const transport = new Transport(options.bundle, validator, () => options.baseUrl, recorder.fetch);
  const started = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
  const steps = {} as Record<Step, StepResult>;

  for (const step of STEPS) {
    recorder.step = step;
    const account = options.accounts[step];
    const office = new Backoffice(
      options.bundle,
      transport,
      new MemoryOnlyVault(),
      'MOSAIQ Desktop F20-02 evidence',
    );
    const signedIn = await office.signIn(account);
    const state = office.state();
    const role = state.status === 'signed_in' ? state.role : null;
    const operation = step === 'staff' ? 'getSale' : 'createCampaign';
    const result: StepResult = {
      account: account.email,
      role,
      signed_in: signedIn.ok,
      may_call: role !== null && operationsForRole(options.bundle, role).includes(operation),
      ok: false,
    };
    if (!signedIn.ok) {
      result.error = signedIn.error;
      steps[step] = result;
      continue;
    }
    try {
      if (step === 'staff') {
        const got = await new Floor(office).getSale(options.saleId);
        const x = recorder.last('GET', `/v1/sales/${options.saleId}`);
        Object.assign(result, { status: x?.status, correlation_id: x?.correlation_id, exchange: x?.number });
        if (got.ok) Object.assign(result, { ok: true, sale: got.value });
        else result.error = got.error;
      } else {
        // The forbidden step sends the same draft the MARKETING step sent: the window would
        // draw no form for this role (may_call false), so the call is made here, in the main
        // process, exactly as the form's submit would make it.
        const got = await new Campaigns(office).createCampaign(options.draft);
        const x = recorder.last('POST', '/v1/campaigns');
        Object.assign(result, { status: x?.status, correlation_id: x?.correlation_id, exchange: x?.number });
        if (got.ok) Object.assign(result, { ok: true, campaign: got.value });
        else result.error = got.error;
      }
    } finally {
      await office.signOut();
    }
    steps[step] = result;
  }

  const evidence: Evidence = {
    started_at: started,
    finished_at: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
    base_url: options.baseUrl,
    sale_id: options.saleId,
    draft: options.draft,
    build: options.build ?? {},
    steps,
  };
  const files: Record<string, string> = {
    'result.json': JSON.stringify(evidence, null, 2) + '\n',
    'exchanges.json': JSON.stringify(recorder.exchanges, null, 2) + '\n',
    'exchanges.md': markdown(evidence, recorder.exchanges),
    ...Object.fromEntries(recorder.bodies),
  };
  // Every file, the kept bodies included, is checked before the first is written.
  for (const [name, text] of Object.entries(files))
    if (recorder.masker.leaks(text))
      throw new UnmaskedSecret(`${name} still holds a secret; nothing written`);
  mkdirSync(join(options.out, 'bodies'), { recursive: true });
  for (const [name, text] of Object.entries(files)) writeFileSync(join(options.out, name), text);
  return evidence;
}

/** The three criteria, as the client saw them. */
export function verdict(e: Evidence): string[] {
  const { staff, marketing, forbidden } = e.steps;
  const failures: string[] = [];
  if (!staff.ok || staff.status !== 200)
    failures.push(`staff read of sale ${e.sale_id}: ${staff.status} ${staff.error?.code ?? ''}`);
  if (!marketing.ok || marketing.status !== 201 || marketing.campaign?.status !== 'DRAFT')
    failures.push(
      `MARKETING draft: ${marketing.status} ${marketing.error?.code ?? marketing.campaign?.status ?? ''}`,
    );
  if (forbidden.may_call)
    failures.push(`the forbidden account's role ${forbidden.role} may call createCampaign`);
  if (forbidden.status !== 403 || forbidden.error?.code !== 'forbidden_role')
    failures.push(`forbidden write: ${forbidden.status} ${forbidden.error?.code ?? ''}`);
  return failures;
}
