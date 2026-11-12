import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import ts from 'typescript';
import { describe, expect, it, vi } from 'vitest';
import { alertFault, Drift, reportFault } from '../../src/main/drift';
import type { DriftAlertView, DriftReportView } from '../../src/shared/api';
import {
  alertSummary,
  axisOf,
  binRange,
  claimText,
  comparisonLabel,
  comparisonNotice,
  count,
  gaugeOf,
  namedOutcome,
  outcomeMethod,
  outcomeVerdict,
  percent,
  ratesChart,
  referenceText,
  reportSummary,
  resultText,
  reviewLabel,
  REVIEW_NOTE,
  sharesChart,
  stateText,
  statisticText,
  THRESHOLDS_NOTE,
  versionText,
  windowText,
} from '../../src/shared/drift';
import {
  mentionsBehaviour,
  mentionsConceptDrift,
  wordingProblems,
  type ScreenWording,
} from '../../src/shared/drift-wording';
import { DRIFT_OPERATIONS, visibleGroups } from '../../src/shared/modules';
import { claims, makeOffice, MemoryVault, tokenResult, type Responder } from '../support/fake';
import { ALERTS, DOCUMENTS, driftAnswer, example, REPORTS } from '../support/drift';

const MR = '/api/model-registry/v1';

function responder(
  role: string,
  override?: (url: URL) => { status: number; body: string } | undefined,
): Responder {
  return (r) => {
    const url = new URL(r.url);
    if (r.method === 'POST' && url.pathname.endsWith('/v1/sessions'))
      return { status: 201, body: tokenResult('a1', 'r1') };
    if (url.pathname.endsWith('/v1/sessions/current'))
      return r.method === 'GET' ? { status: 200, body: claims(role) } : { status: 204 };
    return override?.(url) ?? driftAnswer(url) ?? { status: 500, body: '' };
  };
}

async function setup(role: string, override?: Parameters<typeof responder>[1]) {
  const { office, seen } = makeOffice(
    responder(role, override),
    new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') }),
  );
  await office.signIn({ email: 'a@b.c', password: 'pw' });
  return { drift: new Drift(office), seen, office };
}

const asked = (seen: { url: string }[]): string[] =>
  seen.filter((s) => s.url.includes('/drift-')).map((s) => s.url);

async function reportOf(id: number): Promise<DriftReportView> {
  const { drift } = await setup('ANALYST');
  const res = await drift.getReport(id);
  if (!res.ok) throw new Error(res.error.code);
  return res.value;
}

async function alertOf(id: number): Promise<DriftAlertView> {
  const { drift } = await setup('ANALYST');
  const res = await drift.getAlert(id);
  if (!res.ok) throw new Error(res.error.code);
  return res.value;
}

describe('who sees the module', () => {
  it('offers the module to the roles the four operations allow, and to no other', async () => {
    const names = (ops: string[]): string[] =>
      visibleGroups(ops).flatMap((g) => g.modules.map((m) => m.name));
    for (const role of ['ADMIN', 'ANALYST', 'MARKETING', 'AUDITOR']) {
      const { office } = await setup(role);
      const state = office.state();
      if (state.status !== 'signed_in') throw new Error('not signed in');
      expect(names(state.operations)).toContain('drift');
      for (const op of Object.values(DRIFT_OPERATIONS)) expect(state.operations).toContain(op);
    }
    for (const role of ['STORE_MANAGER', 'INVENTORY_PLANNER']) {
      const { office } = await setup(role);
      const state = office.state();
      if (state.status !== 'signed_in') throw new Error('not signed in');
      expect(names(state.operations)).not.toContain('drift');
    }
  });

  it('reads nothing for a role the contract does not allow, whatever the screen sends', async () => {
    const { drift, seen } = await setup('STORE_MANAGER');
    const before = seen.length;
    for (const refused of [
      await drift.listReports({}),
      await drift.getReport(32),
      await drift.listAlerts({}),
      await drift.getAlert(41),
    ])
      expect(refused).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(seen.length).toBe(before);
  });

  it('checks each call against its own operation', async () => {
    const { drift, office, seen } = await setup('ANALYST');
    const real = office.state();
    if (real.status !== 'signed_in') throw new Error('not signed in');
    vi.spyOn(office, 'state').mockReturnValue({ ...real, operations: ['getDriftReport'] });
    const before = seen.length;
    expect(await drift.listReports({})).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(await drift.listAlerts({})).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(await drift.getAlert(41)).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(seen.length).toBe(before);
    expect(await drift.getReport(32)).toMatchObject({ ok: true });
  });
});

describe('reading reports', () => {
  it('reads the recorded page through the XSD, asking for XML, with the filters the screen sent', async () => {
    const { drift, seen } = await setup('ANALYST');
    const res = await drift.listReports({ kind: 'DATA_DRIFT', model_version_id: 5, page: 1, size: 20 });
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.items.map((r) => [r.report_id, r.kind, r.comparison])).toEqual([
      [32, 'DATA_DRIFT', 'COMPARED'],
      [31, 'DATA_DRIFT', 'COMPARED'],
      [29, 'DATA_DRIFT', 'INSUFFICIENT_DATA'],
      [7, 'DATA_DRIFT', 'NOT_COMPARED'],
    ]);
    expect(res.value.page).toEqual({ page: 1, size: 20, total: 4 });
    const url = new URL(asked(seen)[0] as string);
    expect(url.pathname).toBe(`${MR}/drift-reports`);
    expect([...url.searchParams]).toEqual([
      ['kind', 'DATA_DRIFT'],
      ['model_version_id', '5'],
      ['page', '1'],
      ['size', '20'],
    ]);
    expect(seen.find((s) => s.url.includes('/drift-reports'))?.headers.Accept).toBe('application/xml');
  });

  it('reads a compared report with every variable, its statistics against thresholds and its bins', async () => {
    const r = await reportOf(32);
    expect(r).toMatchObject({
      report_id: 32,
      kind: 'DATA_DRIFT',
      comparison: 'COMPARED',
      model_version: { version_id: 5, name: 'kmeans-k4-2026-10', method: 'KMEANS' },
      window: { days: 30, customer_count: 2400 },
      reference: { sample_size: 18432 },
      previous_report_id: 31,
      alert_ids: [41, 42],
    });
    expect(r.variables.map((v) => [v.variable, v.state])).toEqual([
      ['SEGMENT_SHARES', 'ALARM'],
      ['R', 'ALARM'],
      ['F', 'WATCH'],
      ['M', 'NORMAL'],
    ]);
    const shares = r.variables[0];
    expect(shares?.statistics.map((s) => [s.statistic, s.value, s.level])).toEqual([
      ['PSI', '0.3148', 'ALARM'],
      ['CHI_SQUARE', '676.3754', 'ALARM'],
    ]);
    // Decimals stay exactly as sent, so a p-value far below a double's text is not rounded away.
    expect(shares?.statistics[1]).toMatchObject({
      p_value: '2.78e-146',
      degrees_of_freedom: 3,
      threshold_basis: 'P_VALUE',
      watch_threshold: '0.05',
      alarm_threshold: '0.01',
    });
    expect(shares?.bins.map((b) => b.label)).toEqual(['CHAMPIONS', 'LOYAL', 'AT_RISK', 'LOST']);
    const recency = r.variables[1];
    expect(recency?.bins.at(-1)).toMatchObject({ label: 'NO_VALUE', reference_share: '0.0' });
    expect(recency?.bins[0]).toMatchObject({ lower: '0', upper: '30' });
  });

  it('reads the reports that hold no number as they are, without a statistic', async () => {
    const insufficient = await reportOf(29);
    expect(insufficient).toMatchObject({
      comparison: 'INSUFFICIENT_DATA',
      variables: [],
      window: { customer_count: 187 },
    });
    expect(insufficient.outcome_test).toBeUndefined();
    const crossing = await reportOf(7);
    expect(crossing).toMatchObject({
      comparison: 'NOT_COMPARED',
      not_compared_reason: 'VERSION_CHANGE',
      variables: [],
    });
    expect(crossing.outcome_test).toBeUndefined();
  });

  it('reads the outcome test, with its claim only when a segment persisted', async () => {
    const claimed = await reportOf(36);
    expect(claimed.outcome_test).toMatchObject({
      relation: 'REPURCHASE_GIVEN_SEGMENT',
      horizon_days: 30,
      cohort_as_of: '2026-11-28T03:00:00Z',
      claim: 'CONCEPT_DRIFT',
      thresholds: { alpha: '0.05', min_difference_points: '5', min_customers_per_side: 100 },
    });
    expect(claimed.outcome_test?.segments.map((s) => [s.label_code, s.result, s.persisted])).toEqual([
      ['CHAMPIONS', 'NOT_PASSED', false],
      ['LOYAL', 'PASSED', true],
      ['AT_RISK', 'NOT_PASSED', false],
      ['LOST', 'NOT_PASSED', false],
    ]);
    const first = await reportOf(21);
    expect(first.outcome_test?.claim).toBeUndefined();
    expect(first.outcome_test?.segments.find((s) => s.result === 'PASSED')?.persisted).toBe(false);
  });

  it('shows the service’s not-found answer as returned', async () => {
    const { drift } = await setup('ANALYST');
    expect(await drift.getReport(999)).toMatchObject({
      ok: false,
      error: { code: 'drift_report_not_found' },
    });
    expect(await drift.getAlert(999)).toMatchObject({ ok: false, error: { code: 'drift_alert_not_found' } });
  });

  it('reads an empty page', async () => {
    const { drift } = await setup('ANALYST', (url) =>
      url.pathname === `${MR}/drift-reports`
        ? { status: 200, body: example('drift-reports-none') }
        : undefined,
    );
    const res = await drift.listReports({});
    expect(res).toMatchObject({ ok: true, value: { items: [], page: { total: 0 } } });
  });
});

describe('reading alerts', () => {
  it('reads the recorded page and filters by variable', async () => {
    const { drift, seen } = await setup('AUDITOR');
    const res = await drift.listAlerts({ variable: 'R' });
    if (!res.ok) throw new Error(res.error.code);
    expect(res.value.items.map((a) => [a.alert_id, a.variable, a.claim])).toEqual([
      [42, 'R', 'DISTRIBUTION_CHANGE'],
    ]);
    expect(new URL(asked(seen)[0] as string).searchParams.get('variable')).toBe('R');
  });

  it('reads an alert with its variable, version, evidence and suggested review', async () => {
    const a = await alertOf(41);
    expect(a).toMatchObject({
      alert_id: 41,
      report_id: 32,
      previous_report_id: 31,
      kind: 'DATA_DRIFT',
      claim: 'DISTRIBUTION_CHANGE',
      variable: 'SEGMENT_SHARES',
      model_version: { version_id: 5 },
      suggested_review: { code: 'REVIEW_SEGMENT_MIX' },
    });
    expect(a.statistics).toHaveLength(2);
    expect(a.bins.map((b) => b.label)).toEqual(['LOYAL', 'LOST', 'CHAMPIONS']);
    expect(a.suggested_review.text).toMatch(/does not show that the segments stopped predicting/);
    // One statistic and one element per list still arrive as lists.
    expect((await alertOf(42)).statistics).toHaveLength(1);
  });

  it('reads an outcome-test alert with the segments that persisted', async () => {
    const a = await alertOf(43);
    expect(a).toMatchObject({ kind: 'OUTCOME_TEST', claim: 'CONCEPT_DRIFT', variable: 'REPURCHASE_RATE' });
    expect(a.statistics).toEqual([]);
    expect(a.bins).toEqual([]);
    expect(a.outcome_test?.segments.map((s) => s.label_code)).toEqual(['LOYAL']);
  });
});

describe('what the screen sends', () => {
  it('refuses a value the contract cannot take before anything is sent', async () => {
    const { drift, seen } = await setup('ANALYST');
    const before = seen.length;
    const bad = [
      { kind: 'SEGMENTS' },
      { kind: 1 },
      { model_version_id: 0 },
      { model_version_id: -3 },
      { model_version_id: 1.5 },
      { model_version_id: '3' },
      { comparison: 'GUESSED' },
      { page: 0 },
      { size: 101 },
    ];
    for (const q of bad)
      expect(await drift.listReports(q)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    for (const q of [{ variable: 'CATEGORY' }, { kind: 'x' }, { size: 0 }, 'all'])
      expect(await drift.listAlerts(q)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    for (const id of [0, -1, 1.5, '32', null, undefined, Number.MAX_SAFE_INTEGER + 1]) {
      expect(await drift.getReport(id)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
      expect(await drift.getAlert(id)).toMatchObject({ ok: false, error: { code: 'invalid_input' } });
    }
    expect(seen.length).toBe(before);
  });

  it('puts the identifier in the path as a number and nothing else', async () => {
    const { drift, seen } = await setup('ANALYST');
    await drift.getReport(32);
    await drift.getAlert(42);
    expect(asked(seen).map((u) => new URL(u).pathname)).toEqual([
      `${MR}/drift-reports/32`,
      `${MR}/drift-alerts/42`,
    ]);
  });
});

describe('an answer outside its contract is never used', () => {
  it('refuses the two recorded invalid documents, which the XSD does not accept', async () => {
    for (const [file, call] of [
      ['drift-report-with-unknown-variable.invalid', (d: Drift) => d.getReport(32)],
      ['drift-alert-with-unknown-claim.invalid', (d: Drift) => d.getAlert(41)],
    ] as const) {
      const { drift } = await setup('ANALYST', () => ({ status: 200, body: example(file) }));
      expect(await call(drift)).toMatchObject({ ok: false, error: { code: 'contract_defect' } });
    }
  });

  it('refuses an alert whose claim its own evidence does not support', async () => {
    const text = example('drift-alert');
    // The XSD allows the value; only the wording rule refuses it: a distribution alert is not concept drift.
    const claimed = text.replace('<claim>DISTRIBUTION_CHANGE</claim>', '<claim>CONCEPT_DRIFT</claim>');
    expect(claimed).not.toBe(text);
    const { drift } = await setup('ANALYST', () => ({ status: 200, body: claimed }));
    expect(await drift.getAlert(41)).toMatchObject({ ok: false, error: { code: 'contract_defect' } });
  });

  it('refuses a distribution alert that suggests its review in the words of an outcome result', async () => {
    const text = example('drift-alert').replace(
      'Customers moved out of CHAMPIONS',
      'Concept drift: customers moved out of CHAMPIONS',
    );
    const { drift } = await setup('ANALYST', () => ({ status: 200, body: text }));
    expect(await drift.getAlert(41)).toMatchObject({ ok: false, error: { code: 'contract_defect' } });
  });

  it('refuses a list in which one report breaks the rule, and shows none of it', async () => {
    const page = example('drift-reports-page');
    const broken = page.replace(/<persisted>true<\/persisted>/, '<persisted>false</persisted>');
    expect(broken).not.toBe(page);
    const { drift } = await setup('ANALYST', (url) =>
      url.pathname === `${MR}/drift-reports` ? { status: 200, body: broken } : undefined,
    );
    expect(await drift.listReports({})).toMatchObject({ ok: false, error: { code: 'contract_defect' } });
  });

  it('applies the same rules to a view', async () => {
    const compared = await reportOf(32);
    const insufficient = await reportOf(29);
    const outcome = await reportOf(36);
    const distribution = await alertOf(41);
    const claim = await alertOf(43);
    expect([compared, insufficient, outcome].map(reportFault)).toEqual([null, null, null]);
    expect([distribution, claim].map(alertFault)).toEqual([null, null]);

    expect(reportFault({ ...insufficient, variables: compared.variables })).toMatch(/without a comparison/);
    expect(reportFault({ ...insufficient, outcome_test: outcome.outcome_test })).toMatch(
      /without a comparison/,
    );
    expect(reportFault({ ...insufficient, not_compared_reason: 'VERSION_CHANGE' })).toMatch(/reason/);
    expect(reportFault({ ...compared, outcome_test: outcome.outcome_test })).toMatch(/distribution report/);
    expect(reportFault({ ...compared, variables: [] })).toMatch(/no variable/);
    expect(reportFault({ ...outcome, variables: compared.variables })).toMatch(/outcome test carries/);
    expect(reportFault({ ...outcome, outcome_test: undefined })).toMatch(/no result/);
    expect(
      reportFault({
        ...outcome,
        outcome_test: {
          ...(outcome.outcome_test as NonNullable<typeof outcome.outcome_test>),
          segments: (outcome.outcome_test?.segments ?? []).map((s) => ({ ...s, persisted: false })),
        },
      }),
    ).toMatch(/no segment supports/);

    expect(alertFault({ ...distribution, claim: 'CONCEPT_DRIFT' })).toMatch(/claim does not match/);
    expect(alertFault({ ...claim, claim: 'DISTRIBUTION_CHANGE' })).toMatch(/claim does not match/);
    expect(alertFault({ ...distribution, variable: 'REPURCHASE_RATE' })).toMatch(
      /does not hold distribution/,
    );
    expect(alertFault({ ...distribution, outcome_test: claim.outcome_test })).toMatch(
      /does not hold distribution/,
    );
    expect(alertFault({ ...claim, statistics: distribution.statistics })).toMatch(
      /carries distribution evidence/,
    );
    expect(alertFault({ ...claim, outcome_test: undefined })).toMatch(/without a persisted result/);
  });
});

// ---------- the wording of F23-07 ----------

// The strings of the module that a user or a message can carry. drift-wording.ts holds the rules
// themselves (patterns, and codes naming a problem), not wording, and is exercised below.
const MODULE_SOURCES = [
  'src/shared/drift.ts',
  'src/main/drift.ts',
  'src/renderer/drift.ts',
  'src/renderer/drift-chart.ts',
];

interface Literal {
  file: string;
  text: string;
  /** The function the literal is written in, or (module) for a constant. */
  inside: string;
}

/** Every string a module file can put in front of a user or into a message, with the function it is written in. */
function literalsOf(file: string): Literal[] {
  const source = ts.createSourceFile(
    file,
    readFileSync(join(process.cwd(), file), 'utf8'),
    ts.ScriptTarget.ES2022,
    true,
  );
  const out: Literal[] = [];
  const nameOf = (node: ts.Node): string => {
    for (let p: ts.Node | undefined = node.parent; p !== undefined; p = p.parent) {
      if ((ts.isFunctionDeclaration(p) || ts.isMethodDeclaration(p)) && p.name !== undefined)
        return p.name.getText(source);
      if (
        (ts.isArrowFunction(p) || ts.isFunctionExpression(p)) &&
        ts.isVariableDeclaration(p.parent) &&
        ts.isIdentifier(p.parent.name)
      )
        return p.parent.name.text;
    }
    return '(module)';
  };
  const visit = (node: ts.Node): void => {
    if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node)
    )
      out.push({ file, text: node.text, inside: nameOf(node) });
    ts.forEachChild(node, visit);
  };
  visit(source);
  return out;
}

/** A literal that is an enumeration code (CONCEPT_DRIFT), which names a value and says nothing to a user. */
const isCode = (text: string): boolean => /^[A-Z][A-Z0-9_]*$/.test(text);

describe('the module’s wording follows F23-07, in its source', () => {
  const literals = MODULE_SOURCES.flatMap(literalsOf);

  it('reads the strings of every file of the module', () => {
    expect(literals.length).toBeGreaterThan(200);
    for (const file of MODULE_SOURCES) expect(literals.some((l) => l.file === file)).toBe(true);
  });

  it('writes the phrase concept drift in one function, which words an outcome-test result', () => {
    const phrase = literals.filter((l) => mentionsConceptDrift(l.text) && !isCode(l.text));
    expect(phrase.length).toBeGreaterThan(0);
    expect(phrase.map((l) => `${l.file}:${l.inside}`)).toEqual(
      phrase.map(() => 'src/shared/drift.ts:claimText'),
    );
  });

  it('uses no word of behaviour anywhere in what it says', () => {
    expect(literals.filter((l) => mentionsBehaviour(l.text)).map((l) => l.text)).toEqual([]);
  });

  it('states no verdict about the data or the model', () => {
    const screen: ScreenWording = { claims: [], acrossVersions: false };
    const verdicts = literals.flatMap((l) =>
      wordingProblems([l.text], screen).filter((p) => p.startsWith('verdict')),
    );
    expect(verdicts).toEqual([]);
  });
});

describe('the wording rules catch what they are for', () => {
  const none: ScreenWording = { claims: [], acrossVersions: false };

  it('refuses the phrase anywhere but an outcome-test result that names its evidence', async () => {
    expect(wordingProblems(['Concept drift detected in the segments.'], none)).not.toEqual([]);
    expect(wordingProblems(['The segment shares show concept-drift.'], none)).not.toEqual([]);
    expect(wordingProblems(['A CONCEPT_DRIFT alert.'], none)).not.toEqual([]);
    const claim = await alertOf(43);
    const named = [namedOutcome(claim.outcome_test as NonNullable<typeof claim.outcome_test>)];
    const full = claimText(claim, 'full');
    expect(wordingProblems([full], { claims: named, acrossVersions: false })).toEqual([]);
    // The same sentence on a screen that holds no such result, or with the cohort left out, is refused.
    expect(wordingProblems([full], none)).not.toEqual([]);
    expect(
      wordingProblems([full.replace(/cohort as of [\d-]+/, 'cohort')], {
        claims: named,
        acrossVersions: false,
      }),
    ).not.toEqual([]);
    expect(
      wordingProblems(['Concept drift in the whole population.'], { claims: named, acrossVersions: false }),
    ).not.toEqual([]);
  });

  it('refuses a word of behaviour about a window that crosses a version', () => {
    const across: ScreenWording = { claims: [], acrossVersions: true };
    for (const text of [
      'Customer behaviour changed.',
      'Behavioural shift across the new version.',
      'Customers behave differently now.',
    ])
      expect(wordingProblems([text], across)).not.toEqual([]);
    expect(wordingProblems(['Customer behaviour changed.'], none)).toEqual([]);
    expect(mentionsBehaviour('The service behaved.')).toBe(true);
    expect(mentionsBehaviour('Behaviours')).toBe(true);
  });

  it('refuses a finding worded as a verdict', () => {
    for (const text of [
      'Drift detected.',
      'We detected drift.',
      'The model has drifted.',
      'Model drift found.',
    ])
      expect(wordingProblems([text], none)).not.toEqual([]);
    expect(wordingProblems(['A distribution change. Review the segment mix.'], none)).toEqual([]);
  });
});

describe('claimText', () => {
  it('says the phrase only for an outcome-test result that persisted, and names its evidence beside it', async () => {
    const claim = await alertOf(43);
    const o = claim.outcome_test as NonNullable<typeof claim.outcome_test>;
    for (const form of ['short', 'full'] as const) {
      const text = claimText(claim, form);
      expect(mentionsConceptDrift(text)).toBe(true);
      const named = namedOutcome(o);
      expect(text).toContain(named.relation);
      expect(text).toContain(named.horizon);
      expect(text).toContain(named.cohort);
    }
    expect(claimText(claim, 'full')).toContain('LOYAL');

    const noSegmentPersisted = {
      ...claim,
      outcome_test: { ...o, segments: o.segments.map((s) => ({ ...s, persisted: false })) },
    };
    expect(mentionsConceptDrift(claimText(noSegmentPersisted, 'full'))).toBe(false);
    expect(mentionsConceptDrift(claimText({ ...claim, outcome_test: undefined }, 'short'))).toBe(false);
  });

  it('calls every other finding a distribution change, and says what it does not show', async () => {
    const a = await alertOf(41);
    expect(claimText(a, 'short')).toBe('Distribution change');
    expect(claimText(a, 'full')).toMatch(
      /^Distribution change: the share of customers in each segment differs/,
    );
    expect(claimText(a, 'full')).toContain('model version 5');
    expect(claimText(a, 'full')).toMatch(/does not show that the segments stopped predicting anything/);
    expect(alertSummary(a)).toBe('Distribution change');
    expect(claimText({ ...a, variable: 'M' }, 'full')).toContain('monetary value (M)');
  });
});

/** Every sentence the module can put on a screen for one document: its own words and the service’s. */
function textsOf(view: DriftReportView | DriftAlertView): string[] {
  const out: string[] = [];
  const evidence = (statistics: DriftReportView['variables'][number]['statistics']): void => {
    for (const s of statistics) out.push(statisticText(s));
  };
  if ('report_id' in view && 'comparison' in view) {
    out.push(
      versionText(view.model_version),
      windowText(view.kind, view.window),
      referenceText(view.reference),
      comparisonLabel(view),
      reportSummary(view),
      THRESHOLDS_NOTE,
    );
    const notice = comparisonNotice(view);
    if (notice !== null) out.push(notice);
    for (const v of view.variables) {
      out.push(stateText(v.state));
      evidence(v.statistics);
      out.push(
        sharesChart(v.variable, v.bins).title,
        ...v.bins.map(
          (b) => `${b.label} ${binRange(b)} ${percent(b.reference_share)} ${percent(b.current_share)}`,
        ),
      );
    }
    const o = view.outcome_test;
    if (o !== undefined) {
      out.push(outcomeMethod(o), outcomeVerdict(o), ratesChart(o).title, ...o.segments.map(resultText));
      if (o.claim !== undefined)
        out.push(
          claimText(
            {
              claim: 'CONCEPT_DRIFT',
              variable: 'REPURCHASE_RATE',
              model_version: view.model_version,
              outcome_test: o,
            },
            'full',
          ),
        );
    }
  } else {
    const a = view as DriftAlertView;
    out.push(
      claimText(a, 'short'),
      claimText(a, 'full'),
      versionText(a.model_version),
      windowText(a.kind, a.window),
      referenceText(a.reference),
      reviewLabel(a.suggested_review.code),
      a.suggested_review.text,
      REVIEW_NOTE,
      THRESHOLDS_NOTE,
    );
    evidence(a.statistics);
    if (a.bins.length > 0) out.push(sharesChart(a.variable, a.bins, true).title);
    if (a.outcome_test !== undefined)
      out.push(outcomeMethod(a.outcome_test), ratesChart(a.outcome_test).title);
  }
  return out;
}

describe('the wording of every recorded document', () => {
  it('follows F23-07 for each report and alert the contract records', async () => {
    const { drift } = await setup('ANALYST');
    const views: (DriftReportView | DriftAlertView)[] = [];
    for (const [id, name] of Object.entries(REPORTS)) {
      const res = await drift.getReport(Number(id));
      if (!res.ok) throw new Error(`${name}: ${res.error.code}`);
      views.push(res.value);
    }
    for (const [id, name] of Object.entries(ALERTS)) {
      const res = await drift.getAlert(Number(id));
      if (!res.ok) throw new Error(`${name}: ${res.error.code}`);
      views.push(res.value);
    }
    expect(views).toHaveLength(DOCUMENTS.length);
    for (const view of views) {
      const o = view.outcome_test;
      const crossing = 'comparison' in view && view.comparison === 'NOT_COMPARED';
      const screen: ScreenWording = {
        claims:
          o?.claim === undefined && !('claim' in view && view.claim === 'CONCEPT_DRIFT')
            ? []
            : [namedOutcome(o as NonNullable<typeof o>)],
        acrossVersions: crossing,
      };
      expect(wordingProblems(textsOf(view), screen)).toEqual([]);
    }
  });

  it('gives the phrase to the two outcome-test results that claim it and to nothing else', async () => {
    const { drift } = await setup('ANALYST');
    const phrased: string[] = [];
    for (const id of Object.keys(REPORTS)) {
      const res = await drift.getReport(Number(id));
      if (res.ok && textsOf(res.value).some(mentionsConceptDrift)) phrased.push(`report ${id}`);
    }
    for (const id of Object.keys(ALERTS)) {
      const res = await drift.getAlert(Number(id));
      if (res.ok && textsOf(res.value).some(mentionsConceptDrift)) phrased.push(`alert ${id}`);
    }
    expect(phrased).toEqual(['report 36', 'alert 43']);
  });

  it('says of a window that crosses a version only that nothing was compared', async () => {
    const r = await reportOf(7);
    const notice = comparisonNotice(r) as string;
    expect(notice).toMatch(/change of model version/);
    expect(notice).toMatch(/methodological difference/);
    expect(notice).toMatch(/nothing in this window is compared/);
    expect(mentionsBehaviour(notice)).toBe(false);
    expect(comparisonLabel(r)).toBe('Not compared (change of version)');
    expect(reportSummary(r)).toBe('');
  });

  it('says of too few customers that no figure is given, for each kind', async () => {
    const r = await reportOf(29);
    expect(comparisonNotice(r)).toBe(
      'Insufficient data: the window holds 187 customers and a comparison needs at least 200. No figure is given.',
    );
    expect(comparisonNotice({ ...r, kind: 'OUTCOME_TEST' })).toMatch(/at least 100 customers on both sides/);
    expect(comparisonNotice(await reportOf(32))).toBeNull();
  });

  it('words a first pass of one segment without claiming anything', async () => {
    const first = (await reportOf(21)).outcome_test as NonNullable<
      Awaited<ReturnType<typeof reportOf>>['outcome_test']
    >;
    expect(outcomeVerdict(first)).toMatch(
      /LOYAL passed the test in this cohort\. Nothing is claimed until a segment passes in two consecutive matured cohorts/,
    );
    expect(first.segments.map(resultText)).toEqual([
      'Did not pass',
      'Passed the test, first time',
      'Did not pass',
      'Did not pass',
    ]);
    const claimed = (await reportOf(36)).outcome_test as NonNullable<typeof first>;
    expect(outcomeVerdict(claimed)).toBe('');
    expect(resultText(claimed.segments[1] as (typeof claimed.segments)[number])).toBe('Passed the test');
  });
});

// ---------- numbers behind the charts ----------

describe('chart models', () => {
  it('draws the reference and the current share of every bin on one axis from zero', async () => {
    const r = await reportOf(32);
    const model = sharesChart('R', r.variables[1]?.bins ?? []);
    expect(model.groups.map((g) => [g.label, g.detail, g.referenceText, g.currentText])).toEqual([
      ['B1', '0 to 30', '31.0%', '14.0%'],
      ['B2', '30 to 60', '27.0%', '22.0%'],
      ['B3', '60 to 120', '24.0%', '29.0%'],
      ['B4', '120 to 180', '18.0%', '35.0%'],
      ['No value', '', '0.0%', '0.0%'],
    ]);
    expect(model.max).toBe(0.4);
    expect(model.ticks).toEqual([0, 0.1, 0.2, 0.3, 0.4]);
    expect(Math.max(...model.groups.flatMap((g) => [g.reference, g.current]))).toBeLessThanOrEqual(model.max);
    // The shares of one side sum to 1, as the contract says.
    expect(model.groups.reduce((n, g) => n + g.reference, 0)).toBeCloseTo(1, 6);
    expect(model.groups.reduce((n, g) => n + g.current, 0)).toBeCloseTo(1, 6);
  });

  it('draws the rate of each segment on both sides, and nothing for a side with no customers', async () => {
    const o = (await reportOf(36)).outcome_test as NonNullable<DriftReportView['outcome_test']>;
    const model = ratesChart(o);
    expect(model.groups.map((g) => [g.label, g.referenceText, g.currentText])).toEqual([
      ['CHAMPIONS', '62.0%', '59.5%'],
      ['LOYAL', '48.0%', '38.1%'],
      ['AT_RISK', '27.0%', '26.5%'],
      ['LOST', '8.0%', '8.3%'],
    ]);
    const empty = ratesChart({
      ...o,
      segments: [
        { ...o.segments[0], current: { customers: 0, repurchasers: 0 } } as (typeof o.segments)[number],
      ],
    });
    expect(empty.groups[0]).toMatchObject({ current: 0, currentText: 'no customers' });
  });

  it('rounds the axis up to a clean top', () => {
    expect(axisOf([])).toEqual({ max: 0.05, ticks: [0, 0.05] });
    expect(axisOf([0.12]).max).toBe(0.15);
    expect(axisOf([0.35]).max).toBe(0.4);
    expect(axisOf([0.62]).max).toBe(0.8);
    expect(axisOf([1]).max).toBe(1);
    expect(axisOf([0.4, 0.1]).ticks).toEqual([0, 0.1, 0.2, 0.3, 0.4]);
  });

  it('puts a PSI against its two thresholds on one scale, and a p-value on none', async () => {
    const [psi, chi] = (await reportOf(32)).variables[0]?.statistics ?? [];
    expect(gaugeOf(psi as NonNullable<typeof psi>)).toEqual({
      value: 0.3148,
      watch: 0.1,
      alarm: 0.25,
      max: 0.5,
      level: 'ALARM',
    });
    expect(gaugeOf(chi as NonNullable<typeof chi>)).toBeNull();
    // A value far past the alarm threshold still fits the scale.
    expect(gaugeOf({ ...(psi as NonNullable<typeof psi>), value: '2.0' })?.max).toBeCloseTo(2.2, 6);
  });

  it('formats counts and shares the same on every machine', () => {
    expect(count(18432)).toBe('18,432');
    expect(count(187)).toBe('187');
    expect(count(1234567)).toBe('1,234,567');
    expect(percent('0.3148')).toBe('31.5%');
    expect(percent('0.0')).toBe('0.0%');
    expect(percent(undefined)).toBe('no customers');
  });
});
