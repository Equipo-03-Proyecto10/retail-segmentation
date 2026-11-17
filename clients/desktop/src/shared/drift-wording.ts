// The wording rules of F23-07 as code (ADR-0065, ADR-0064), so a screen, a main-process check and a
// test apply the same ones. This file holds patterns and codes only: it carries no sentence a screen
// shows, so the test that scans the module's strings never reads the rules as the module's wording.
//
// The rules:
//   1. "Concept drift" may appear only on a result of the outcome test, with the relation, horizon
//      and cohort named beside it. Anywhere else the phrase is "distribution change".
//   2. A window that crosses a change of model version is never compared, so nothing said about it
//      uses a word of behaviour: a methodological difference is not behaviour.
//   3. A finding is a suggestion to review, never a verdict on the data or the model.

const CONCEPT_DRIFT = /concept[\s_-]*drift/i;
const BEHAVIOUR = /\bbehaviou?r(?:s|al|ally)?\b|\bbehav(?:e|es|ed|ing)\b/i;
const VERDICTS: readonly RegExp[] = [
  /\bdrift\s+(?:was\s+|is\s+|has\s+been\s+)?(?:detected|confirmed|found|proven)\b/i,
  /\b(?:detected|confirmed|found|proven)\s+(?:a\s+|the\s+)?(?:concept\s+|data\s+)?drift\b/i,
  /\bthe\s+model\s+(?:has\s+|have\s+)?(?:drifted|failed|is\s+(?:wrong|broken|stale|obsolete|invalid))\b/i,
  /\bmodel\s+drift\b/i,
];

export function mentionsConceptDrift(text: string): boolean {
  return CONCEPT_DRIFT.test(text);
}

export function mentionsBehaviour(text: string): boolean {
  return BEHAVIOUR.test(text);
}

/** What an outcome-test result must name beside the phrase "concept drift". */
export interface NamedOutcome {
  relation: string;
  horizon: string;
  cohort: string;
}

/** What a screen is entitled to say, from the evidence it holds. */
export interface ScreenWording {
  /** Outcome-test results on the screen whose claim is CONCEPT_DRIFT: the only text that may carry the phrase. */
  claims: readonly NamedOutcome[];
  /** The screen shows a window that crosses a change of model version. */
  acrossVersions: boolean;
}

/**
 * The problems in the texts of one screen, as codes followed by the text at fault. Empty when the
 * screen follows F23-07. A text that holds the phrase must be a result of the outcome test, so it
 * must name one of the claims' relation, horizon and cohort in that same text.
 */
export function wordingProblems(texts: readonly string[], screen: ScreenWording): string[] {
  const problems: string[] = [];
  for (const text of texts) {
    if (mentionsConceptDrift(text)) {
      const named = screen.claims.some(
        (c) => text.includes(c.relation) && text.includes(c.horizon) && text.includes(c.cohort),
      );
      if (!named) problems.push(`concept-drift-outside-an-outcome-result: ${text}`);
    }
    if (screen.acrossVersions && mentionsBehaviour(text))
      problems.push(`behaviour-word-across-versions: ${text}`);
    if (VERDICTS.some((v) => v.test(text))) problems.push(`verdict: ${text}`);
  }
  return problems;
}
