import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// A stand-in for model-registry's drift operations, built from the recorded examples of
// contracts/model-registry/ (F23-08). The documents are the examples as recorded. The list of
// alerts is cut from the recorded page, and the list of reports is every recorded report, newest
// window first (the recorded page holds three of the six), each filtered, then paged, so every
// answer still validates against model-registry.xsd. The four operations are marked planned (ADR-0073); this is what the desktop
// client's tests answer with until the service implements them.

// Both runners start in clients/desktop, so the contracts are two directories up.
const CONTRACTS = join(process.cwd(), '..', '..', 'contracts');

export const example = (name: string): string =>
  readFileSync(join(CONTRACTS, 'model-registry', 'examples', `${name}.xml`), 'utf8');

/** Report id to the recorded example of that report. */
export const REPORTS: Readonly<Record<number, string>> = {
  7: 'drift-report-not-compared',
  21: 'drift-report-outcome-test-first-cohort',
  29: 'drift-report-insufficient-data',
  31: 'drift-report-alarm-pending',
  32: 'drift-report',
  36: 'drift-report-outcome-test',
};

/** Alert id to the recorded example of that alert. */
export const ALERTS: Readonly<Record<number, string>> = {
  41: 'drift-alert',
  42: 'drift-alert-feature',
  43: 'drift-alert-outcome-test',
};

/** Every recorded document a screen can show, by name, with the kind of document it is. */
export const DOCUMENTS: readonly { name: string; root: 'DriftReport' | 'DriftAlert' }[] = [
  ...Object.values(REPORTS).map((name) => ({ name, root: 'DriftReport' as const })),
  ...Object.values(ALERTS).map((name) => ({ name, root: 'DriftAlert' as const })),
];

const MR = '/api/model-registry/v1';

/** A number from a request, made safe to put in markup (CodeQL js/reflected-xss): a fake must not echo text. */
const whole = (value: string | null, fallback: number): string =>
  String(Math.max(1, Math.trunc(Number(value ?? fallback)) || fallback));

function blocks(xml: string, tag: string): string[] {
  return xml.match(new RegExp(`<${tag}>[\\s\\S]*?</${tag}>`, 'g')) ?? [];
}

/** One recorded report as an item of a list: the document without its prolog and its namespace declaration. */
function item(name: string): string {
  return example(name)
    .replace(/<\?xml[^>]*\?>\s*/, '')
    .replace(/^<DriftReport xmlns="[^"]*">/, '<DriftReport>');
}

const endOf = (block: string): string => /<end>([^<]*)<\/end>/.exec(block)?.[1] ?? '';

/** Every recorded report, newest window first, as the items of a list. */
function allReports(): string[] {
  return Object.values(REPORTS)
    .map(item)
    .sort((a, b) => endOf(b).localeCompare(endOf(a)));
}

/** A list cut to the items that pass `keep`, then to the page asked for. */
function cut(
  xml: string,
  all0: string[],
  tag: 'DriftReport' | 'DriftAlert',
  q: URLSearchParams,
  keep: (block: string) => boolean,
): string {
  const all = all0.filter(keep);
  const size = Number(whole(q.get('size'), 20));
  const page = Number(whole(q.get('page'), 1));
  const shown = all.slice((page - 1) * size, page * size);
  const root = tag === 'DriftReport' ? 'DriftReports' : 'DriftAlerts';
  const open = xml.slice(0, xml.search(/<(DriftReport|DriftAlert)>/));
  const info = `<common:PageInfo><common:page>${page}</common:page><common:size>${size}</common:size><common:total>${all.length}</common:total></common:PageInfo>`;
  return `${open}${shown.join('\n')}${info}</${root}>`;
}

const has = (block: string, tag: string, value: string | null): boolean =>
  value === null || block.includes(`<${tag}>${value}</${tag}>`);

export interface Answer {
  status: number;
  body: string;
}

/** The answer to a drift request, or undefined when the request is not a drift one. */
export function driftAnswer(url: URL): Answer | undefined {
  if (!url.pathname.startsWith(`${MR}/drift-`)) return undefined;
  const q = url.searchParams;
  const version = q.get('model_version_id');
  if (url.pathname === `${MR}/drift-reports`)
    return {
      status: 200,
      body: cut(example('drift-reports-page'), allReports(), 'DriftReport', q, (b) => {
        const model = /<model_version>[\s\S]*?<version_id>(\d+)<\/version_id>/.exec(b)?.[1] ?? '';
        return (
          has(b, 'kind', q.get('kind')) &&
          has(b, 'comparison', q.get('comparison')) &&
          (version === null || model === version)
        );
      }),
    };
  if (url.pathname === `${MR}/drift-alerts`)
    return {
      status: 200,
      body: cut(
        example('drift-alerts-page'),
        blocks(example('drift-alerts-page'), 'DriftAlert'),
        'DriftAlert',
        q,
        (b) => {
          const model = /<model_version>[\s\S]*?<version_id>(\d+)<\/version_id>/.exec(b)?.[1] ?? '';
          return (
            has(b, 'kind', q.get('kind')) &&
            has(b, 'variable', q.get('variable')) &&
            (version === null || model === version)
          );
        },
      ),
    };
  const report = /\/drift-reports\/(\d+)$/.exec(url.pathname)?.[1];
  if (report !== undefined) {
    const name = REPORTS[Number(report)];
    return name === undefined
      ? { status: 404, body: example('drift-report-not-found') }
      : { status: 200, body: example(name) };
  }
  const alert = /\/drift-alerts\/(\d+)$/.exec(url.pathname)?.[1];
  if (alert !== undefined) {
    const name = ALERTS[Number(alert)];
    return name === undefined
      ? { status: 404, body: example('drift-alert-not-found') }
      : { status: 200, body: example(name) };
  }
  return { status: 404, body: '' };
}
