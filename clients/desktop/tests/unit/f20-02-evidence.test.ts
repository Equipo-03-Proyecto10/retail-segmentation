import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { collect, Masker, verdict, type Options } from '../../scripts/f20-02-evidence';
import { bundle, claims, errorXml, example, fakeFetch, tokenResult, type Responder } from '../support/fake';

// F20-02 (#760): what the evidence script records around the main-process calls. What a live
// gateway answers is the runbook's (docs/evidence/f20-02-electron-sale-campaign.md); these cases
// pin the recording: the three steps, the exact bodies kept for xmllint, and no secret written.

const ACCESS = 'eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJzdGFmZiJ9.c2lnbmF0dXJl';
const REFRESH = 'opaque-refresh-secret';
const PASSWORD = 'not-a-real-password';
const ROLE: Record<string, string> = {
  'staff@test': 'STORE_MANAGER',
  'marketing@test': 'MARKETING',
  'analyst@test': 'ANALYST',
};

/** Answers like auth, sales, campaigns and the name lookups, from the recorded examples. */
function gateway(forbiddenAnswer = errorXml('forbidden_role')): Responder {
  let signedIn = '';
  return (r) => {
    const path = new URL(r.url).pathname;
    if (r.method === 'POST' && path === '/api/auth/v1/sessions') {
      signedIn = /<email>([^<]+)<\/email>/.exec(r.body ?? '')?.[1] ?? '';
      return { status: 201, body: tokenResult(ACCESS, REFRESH) };
    }
    if (path === '/api/auth/v1/sessions/current')
      return r.method === 'DELETE' ? { status: 204 } : { status: 200, body: claims(ROLE[signedIn] ?? '') };
    if (path === '/api/sales/v1/sales/4812') return { status: 200, body: example('sales', 'get-sale') };
    if (path === '/api/products/v1/products')
      return { status: 200, body: example('products', 'products-50-id-batch') };
    if (path === '/api/stores/v1/stores') return { status: 200, body: example('stores', 'directory-page') };
    if (path === '/api/sales/v1/channels') return { status: 200, body: example('sales', 'list-channels') };
    if (r.method === 'POST' && path === '/api/campaigns/v1/campaigns')
      return ROLE[signedIn] === 'MARKETING'
        ? { status: 201, body: example('campaigns', 'campaign') }
        : { status: 403, body: forbiddenAnswer };
    return { status: 404, body: errorXml('not_found') };
  };
}

const made: string[] = [];
afterEach(() => {
  for (const dir of made.splice(0)) rmSync(dir, { recursive: true, force: true });
});

function options(responder: Responder): Options {
  const out = mkdtempSync(join(tmpdir(), 'f20-02-'));
  made.push(out);
  return {
    bundle,
    baseUrl: 'http://gw.test',
    out,
    saleId: 4812,
    draft: {
      name: 'Holiday Loyalty Preview',
      label_code: 'LOYAL',
      starts_on: '2026-11-01',
      ends_on: '2026-12-31',
    },
    accounts: {
      staff: { email: 'staff@test', password: PASSWORD },
      marketing: { email: 'marketing@test', password: PASSWORD },
      forbidden: { email: 'analyst@test', password: PASSWORD },
    },
    fetchImpl: fakeFetch(responder).fetch as never,
  };
}

describe('the F20-02 evidence script', () => {
  it('records the three steps through the main-process calls, and they hold', async () => {
    const o = options(gateway());
    const e = await collect(o);
    expect(e.steps.staff).toMatchObject({ role: 'STORE_MANAGER', may_call: true, ok: true, status: 200 });
    expect(e.steps.staff.sale?.total).toBeDefined();
    expect(e.steps.marketing).toMatchObject({ role: 'MARKETING', may_call: true, ok: true, status: 201 });
    expect(e.steps.marketing.campaign).toMatchObject({ campaign_id: 301, status: 'DRAFT' });
    expect(e.steps.forbidden).toMatchObject({ role: 'ANALYST', may_call: false, ok: false, status: 403 });
    expect(e.steps.forbidden.error).toMatchObject({ code: 'forbidden_role', correlation_id: 'srv-1' });
    expect(verdict(e)).toEqual([]);
  });

  it('keeps the exact bytes of every non-auth body, and the same request for both writes', async () => {
    const o = options(gateway());
    const e = await collect(o);
    const exchanges = JSON.parse(readFileSync(join(o.out, 'exchanges.json'), 'utf8')) as {
      number: number;
      url: string;
      request_file?: string;
      response_file?: string;
    }[];
    const files = readdirSync(join(o.out, 'bodies'));
    for (const x of exchanges.filter((x) => new URL(x.url).pathname === '/api/auth/v1/sessions'))
      expect(x.request_file ?? x.response_file).toBeUndefined();
    const sale = exchanges[(e.steps.staff.exchange as number) - 1];
    expect(readFileSync(join(o.out, sale!.response_file!), 'utf8')).toBe(example('sales', 'get-sale'));
    const written = (step: 'marketing' | 'forbidden'): string =>
      readFileSync(join(o.out, exchanges[(e.steps[step].exchange as number) - 1]!.request_file!), 'utf8');
    expect(written('forbidden')).toBe(written('marketing'));
    expect(files.length).toBeGreaterThan(4);
  });

  it('writes no password, no token and no refresh credential', async () => {
    const o = options(gateway());
    await collect(o);
    for (const name of ['result.json', 'exchanges.json', 'exchanges.md']) {
      const text = readFileSync(join(o.out, name), 'utf8');
      expect(text).not.toContain(PASSWORD);
      expect(text).not.toContain(ACCESS);
      expect(text).not.toContain(REFRESH);
    }
    for (const name of readdirSync(join(o.out, 'bodies'))) {
      const text = readFileSync(join(o.out, 'bodies', name), 'utf8');
      expect(text).not.toContain(ACCESS);
      expect(text).not.toContain(REFRESH);
    }
  });

  it('reports a forbidden write that was not refused as not held', async () => {
    const e = await collect(options(gateway(errorXml('invalid_body'))));
    expect(verdict(e)).toEqual([expect.stringContaining('forbidden write: 403 invalid_body')]);
  });

  it('masks secrets in XML elements, Authorization and anything shaped like a token', () => {
    const m = new Masker();
    expect(m.header('Authorization', `Bearer ${ACCESS}`)).toBe('Bearer <masked #1>');
    expect(m.body(`<a:password>${PASSWORD}</a:password><refresh_token>${REFRESH}</refresh_token>`)).toBe(
      '<a:password><masked #2></a:password><refresh_token><masked #3></refresh_token>',
    );
    expect(m.body(`free text ${ACCESS}`)).toBe('free text <masked #1>');
  });
});
