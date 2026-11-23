import { describe, expect, it } from 'vitest';
import { PREVIEW_LIMIT, RfmRules } from '../../src/main/rfm-rules';
import type { RfmRuleSetDraft } from '../../src/shared/api';
import { claims, errorXml, example, makeOffice, MemoryVault, tokenResult } from '../support/fake';
import type { Responder, Seen } from '../support/fake';

// The RFM configuration module's main-process half (F28-04, #873), against the recorded examples of
// contracts/rfm-engine/. createRfmRuleSet and getRfmRuleSet are marked planned in the contract, so the
// service answers none of them yet; every answer here is a recorded example.

const ENGINE = '/api/rfm-engine/v1';

async function signedIn(role: string, responder: Responder): Promise<{ rules: RfmRules; seen: Seen[] }> {
  const { office, seen } = makeOffice(
    (r) => {
      if (r.method === 'POST' && r.url.endsWith('/v1/sessions'))
        return { status: 201, body: tokenResult('a1', 'r1') };
      if (r.url.endsWith('/v1/sessions/current'))
        return r.method === 'GET' ? { status: 200, body: claims(role) } : { status: 204 };
      return responder(r);
    },
    new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') }),
  );
  await office.signIn({ email: 'a@b.c', password: 'pw' });
  seen.length = 0;
  return { rules: new RfmRules(office), seen };
}

const paths = (seen: Seen[]): string[] =>
  seen.map((s) => `${s.method} ${new URL(s.url).pathname}${new URL(s.url).search}`);

/** The leaf elements of a request body, in order. */
const fieldsOf = (xml: string): [string, string][] =>
  [...xml.matchAll(/<([a-z_]+)>([^<]*)<\/\1>/g)].map((m) => [m[1] as string, m[2] as string]);

/** A draft whose bands do not overlap, so it passes the checks the contract's own example fails. */
const DRAFT: RfmRuleSetDraft = {
  name: 'Quarterly bands 2026-Q4',
  window_days: 180,
  recency_boundaries: [14, 35, 70, 120],
  frequency_boundaries: [8, 5, 3, 2],
  monetary_boundaries: ['1200.00', '800.00', '450.00', '200.00'],
  bands: [
    { label_code: 'CHAMPION', r_min: 5, r_max: 5, f_min: 5, f_max: 5, m_min: 4, m_max: 5 },
    { label_code: 'LOYAL', r_min: 4, r_max: 4, f_min: 4, f_max: 5, m_min: 3, m_max: 5 },
    { label_code: 'AT_RISK', r_min: 2, r_max: 3, f_min: 4, f_max: 5, m_min: 1, m_max: 5 },
    { label_code: 'LOST', r_min: 1, r_max: 1, f_min: 1, f_max: 3, m_min: 1, m_max: 2 },
  ],
  fallback_label_code: 'LOST',
};

const byExample =
  (table: Record<string, [number, string]>): Responder =>
  (r) => {
    const path = new URL(r.url).pathname;
    for (const [suffix, [status, file]] of Object.entries(table))
      if (path.endsWith(suffix)) return { status, body: example('rfm-engine', file) };
    return { status: 404 };
  };

describe('reading rule sets', () => {
  it('lists newest first with the published filter and reads bands and cut-points', async () => {
    const { rules, seen } = await signedIn(
      'ANALYST',
      byExample({ '/rfm-rule-sets': [200, 'rule-sets-page'] }),
    );
    const res = await rules.listRuleSets({ published: true, page: 1, size: 50 });
    expect(paths(seen)).toEqual([`GET ${ENGINE}/rfm-rule-sets?published=true&page=1&size=50`]);
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.page.total).toBe(2);
    const first = res.value.items[0];
    expect(first?.rule_set_id).toBe(4);
    expect(first?.recency_boundaries).toEqual([14, 35, 70, 120]);
    expect(first?.monetary_boundaries).toEqual(['1200.00', '800.00', '450.00', '200.00']);
    expect(first?.bands.map((b) => b.label_code)).toEqual(['CHAMPION', 'LOYAL', 'AT_RISK', 'LOST']);
    expect(first?.bands[0]).toEqual({
      label_code: 'CHAMPION',
      r_min: 5,
      r_max: 5,
      f_min: 5,
      f_max: 5,
      m_min: 4,
      m_max: 5,
    });
  });

  it('reads one rule set, with its published state and no creator for a seeded one', async () => {
    const { rules, seen } = await signedIn('MARKETING', byExample({ '/rfm-rule-sets/3': [200, 'rule-set'] }));
    const res = await rules.getRuleSet(3);
    expect(paths(seen)).toEqual([`GET ${ENGINE}/rfm-rule-sets/3`]);
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value).toMatchObject({ rule_set_id: 3, published: true, window_days: 180 });
    expect(res.value).not.toHaveProperty('created_by');
  });

  it('refuses a published filter that is not yes or no, and a role without the operation', async () => {
    const { rules, seen } = await signedIn('ANALYST', byExample({}));
    const bad = await rules.listRuleSets({ published: 'yes' });
    expect(bad).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    expect(seen).toHaveLength(0);
    const { rules: other, seen: none } = await signedIn('STORE_MANAGER', byExample({}));
    expect(await other.listRuleSets({})).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(await other.getRuleSet(3)).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(none).toHaveLength(0);
  });

  it('shows the service’s answer for a rule set that does not exist', async () => {
    const { rules } = await signedIn(
      'ANALYST',
      byExample({ '/rfm-rule-sets/99': [404, 'rule-set-not-found'] }),
    );
    expect(await rules.getRuleSet(99)).toMatchObject({ ok: false, error: { code: 'rule_set_not_found' } });
  });
});

describe('previewing a draft', () => {
  const answer = byExample({
    '/rfm-rule-sets/3': [200, 'rule-set-published'],
    '/rfm-profiles': [200, 'profiles-page'],
  });

  it('reads the scored profiles of the basis rule set and counts them under the draft', async () => {
    const { rules, seen } = await signedIn('ANALYST', answer);
    const res = await rules.previewRuleSet(DRAFT, 3);
    expect(paths(seen)).toEqual([
      `GET ${ENGINE}/rfm-rule-sets/3`,
      `GET ${ENGINE}/rfm-profiles?state=scored&rule_set_id=3&page=1&size=100`,
    ]);
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.basis).toEqual({
      rule_set_id: 3,
      name: 'Seeded bands, 180-day window',
      window_days: 180,
    });
    expect(res.value.draft_window_days).toBe(180);
    // The recorded page holds three profiles; only the scored one is counted (R 30, F 9, M 842.50).
    expect(res.value.customers).toBe(1);
    expect(res.value.labels).toEqual([
      { label_code: 'LOYAL', customers: 1, via_fallback: 0, share: '1.0000' },
    ]);
  });

  it('sends nothing for a draft the contract refuses, and names its band', async () => {
    const { rules, seen } = await signedIn('ANALYST', answer);
    const overlapping = structuredClone(DRAFT);
    (overlapping.bands[1] as { r_max: number }).r_max = 5;
    const res = await rules.previewRuleSet(overlapping, 3);
    expect(seen).toHaveLength(0);
    if (res.ok) throw new Error('a refusal was expected');
    expect(res.error.code).toBe('invalid_body');
    expect(res.error.message).toContain('Band 1 (CHAMPION) and Band 2 (LOYAL) overlap');
    expect(res.error.details).toEqual([{ field: 'bands[1]', code: 'bands_overlap' }]);
  });

  it('refuses a base too large to count whole rather than showing a part of it', async () => {
    const big = (
      await signedIn('ANALYST', (r) => {
        const path = new URL(r.url).pathname;
        if (path.endsWith('/rfm-rule-sets/3'))
          return { status: 200, body: example('rfm-engine', 'rule-set-published') };
        return {
          status: 200,
          body: example('rfm-engine', 'profiles-page').replace(
            '<common:total>3</common:total>',
            `<common:total>${PREVIEW_LIMIT + 1}</common:total>`,
          ),
        };
      })
    ).rules;
    expect(await big.previewRuleSet(DRAFT, 3)).toMatchObject({
      ok: false,
      error: { code: 'too_many_profiles' },
    });
  });

  it('needs the role to read profiles and rule sets', async () => {
    const { rules, seen } = await signedIn('STORE_MANAGER', answer);
    expect(await rules.previewRuleSet(DRAFT, 3)).toMatchObject({
      ok: false,
      error: { code: 'forbidden_role' },
    });
    expect(seen).toHaveLength(0);
  });
});

describe('storing a draft', () => {
  const created = byExample({ '/rfm-rule-sets': [201, 'rule-set-draft'] });

  it('sends the contract’s request, in its order, and answers with the unpublished rule set', async () => {
    const { rules, seen } = await signedIn('ADMIN', created);
    const res = await rules.createRuleSet(DRAFT);
    expect(paths(seen)).toEqual([`POST ${ENGINE}/rfm-rule-sets`]);
    const body = seen[0]?.body as string;
    expect(body.startsWith('<?xml version="1.0" encoding="UTF-8"?><RfmRuleSetRequest xmlns="')).toBe(true);
    expect(fieldsOf(body).map(([name]) => name)).toEqual([
      'name',
      'window_days',
      'recency_boundaries',
      'recency_boundaries',
      'recency_boundaries',
      'recency_boundaries',
      'frequency_boundaries',
      'frequency_boundaries',
      'frequency_boundaries',
      'frequency_boundaries',
      'monetary_boundaries',
      'monetary_boundaries',
      'monetary_boundaries',
      'monetary_boundaries',
      ...Array.from({ length: 4 }, () => [
        'label_code',
        'r_min',
        'r_max',
        'f_min',
        'f_max',
        'm_min',
        'm_max',
      ]).flat(),
      'fallback_label_code',
    ]);
    expect(fieldsOf(body).slice(0, 2)).toEqual([
      ['name', 'Quarterly bands 2026-Q4'],
      ['window_days', '180'],
    ]);
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.published).toBe(false);
  });

  it('refuses an analyst, who may read and preview but not store, before anything is sent', async () => {
    const { rules, seen } = await signedIn('ANALYST', created);
    expect(await rules.createRuleSet(DRAFT)).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(seen).toHaveLength(0);
  });

  it('refuses a draft the contract refuses and sends nothing', async () => {
    const { rules, seen } = await signedIn('ADMIN', created);
    const bad = structuredClone(DRAFT);
    bad.window_days = 4000;
    bad.recency_boundaries = [14, 10, 70, 120];
    const res = await rules.createRuleSet(bad);
    expect(seen).toHaveLength(0);
    if (res.ok) throw new Error('a refusal was expected');
    expect(res.error.details).toEqual([
      { field: 'window_days', code: 'out_of_range' },
      { field: 'recency_boundaries[1]', code: 'not_monotonic' },
    ]);
    expect(res.error.message).toContain('Window: 4000 days is outside 1 to 3650 days.');
    expect(await rules.createRuleSet('not a draft')).toMatchObject({
      ok: false,
      error: { code: 'invalid_input' },
    });
    expect(await rules.createRuleSet({ ...DRAFT, bands: 'x' })).toMatchObject({
      ok: false,
      error: { code: 'invalid_input' },
    });
  });

  it('shows the service’s refusals: a name already used, and bands it finds overlapping', async () => {
    const taken = await signedIn('ADMIN', byExample({ '/rfm-rule-sets': [409, 'rule-set-name-taken'] }));
    expect(await taken.rules.createRuleSet(DRAFT)).toMatchObject({
      ok: false,
      error: { code: 'rule_set_name_taken' },
    });
    const invalid = await signedIn('ADMIN', () => ({
      status: 422,
      body: errorXml(
        'invalid_body',
        'Each band must be ordered and no two bands may overlap.',
        '<details><field>bands</field><code>bands_overlap</code></details>',
      ),
    }));
    expect(await invalid.rules.createRuleSet(DRAFT)).toMatchObject({
      ok: false,
      error: { code: 'invalid_body', details: [{ field: 'bands', code: 'bands_overlap' }] },
    });
  });
});
