import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// A stand-in for segments-service's migration operations, built from the recorded examples of
// contracts/segments/ (F22-14). The matrix and the explanations are the examples as recorded; the
// customers behind a cell, or behind a label's entries and exits, are generated from the matrix's own
// cells, so a list holds exactly as many customers as the cell counted and every answer still
// validates against segments.xsd. The service operations are marked planned (ADR-0073); this is what
// the desktop client's tests answer with until they are implemented.

// Both runners start in clients/desktop, so the contracts are two directories up.
const CONTRACTS = join(process.cwd(), '..', '..', 'contracts');
export const SEGMENTS_NS = 'https://mosaiq.maxthecoder.online/xml/segments/v1';
export const COMMON_NS = 'https://mosaiq.maxthecoder.online/xml/common/v1';

export const example = (name: string): string =>
  readFileSync(join(CONTRACTS, 'segments', 'examples', `${name}.xml`), 'utf8');

/** within: both dates under model version 3. across: version 3 against version 4 (methodological). */
export type Kind = 'within' | 'across';

/** The recorded matrix windows: 2026-09-14 to 2026-10-12 (one version) and 2026-10-12 to 2026-10-21 (two). */
export const kindOf = (to: string): Kind => (to >= '2026-10-13' ? 'across' : 'within');

export interface Ref {
  state: string;
  code?: string;
}
export interface Cell {
  previous: Ref;
  next: Ref;
  movement: string;
  count: number;
}
export interface Total {
  ref: Ref;
  before: number;
  after: number;
  entries: number;
  exits: number;
}

/** A value from a request, made safe to put in markup (CodeQL js/reflected-xss): a fake must not echo it raw. */
export const escapeXml = (value: string): string =>
  value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');

const tag = (xml: string, name: string): string | undefined =>
  new RegExp(`<${name}>([^<]*)</${name}>`).exec(xml)?.[1];

export const matrixXml = (kind: Kind): string =>
  example(kind === 'within' ? 'migration-matrix' : 'migration-matrix-across-versions');

export function cellsOf(xml: string): Cell[] {
  return (xml.match(/<MigrationCell>[\s\S]*?<\/MigrationCell>/g) ?? []).map((block) => ({
    previous: { state: tag(block, 'previous_state') ?? '', code: tag(block, 'previous_label_code') },
    next: { state: tag(block, 'new_state') ?? '', code: tag(block, 'new_label_code') },
    movement: tag(block, 'movement') ?? '',
    count: Number(tag(block, 'customer_count')),
  }));
}

export function totalsOf(xml: string): Total[] {
  return (xml.match(/<MigrationTotal>[\s\S]*?<\/MigrationTotal>/g) ?? []).map((block) => ({
    ref: { state: tag(block, 'state') ?? '', code: tag(block, 'label_code') },
    before: Number(tag(block, 'before_count')),
    after: Number(tag(block, 'after_count')),
    entries: Number(tag(block, 'entries')),
    exits: Number(tag(block, 'exits')),
  }));
}

const same = (r: Ref, state: string | null, code: string | null): boolean =>
  r.state === state && (r.code ?? null) === code;

/** The customer the recorded explanations are about, one per kind of matrix. */
export const MOVER = {
  within: { id: '7b3ad781-c5de-4f40-ae50-1fa825ce2b25', previous: 'LOYAL', next: 'CHAMPION' },
  across: { id: 'e4d1b7a2-93c6-4f08-b5a9-2c6d8e0f1a37', previous: 'CHAMPION', next: 'LOYAL' },
} as const;

const uuid = (n: number): string => `00000000-0000-4000-8000-${n.toString(16).padStart(12, '0')}`;

function migration(kind: Kind, cell: Cell, index: number, serial: number): string {
  const mover = MOVER[kind];
  const isMover = index === 0 && cell.previous.code === mover.previous && cell.next.code === mover.next;
  const id = isMover ? mover.id : uuid(serial);
  const across = kind === 'across';
  const label = cell.previous.state === 'label' && cell.next.state === 'label';
  const moved = label && cell.movement !== 'unchanged';
  // Alternate customers: one whose recency and monetary changed, one whose rank alone moved.
  const rankOnly = moved && (isMover ? across : serial % 2 === 0);
  const variables = moved && !rankOnly ? ['recency', 'monetary'] : [];
  return [
    '<SegmentMigration>',
    `<customer_id>${id}</customer_id>`,
    `<previous_state>${cell.previous.state}</previous_state>`,
    cell.previous.code ? `<previous_label_code>${cell.previous.code}</previous_label_code>` : '',
    `<new_state>${cell.next.state}</new_state>`,
    cell.next.code ? `<new_label_code>${cell.next.code}</new_label_code>` : '',
    `<movement>${cell.movement}</movement>`,
    moved ? '<changed_at>2026-10-05T11:40:02Z</changed_at>' : '',
    moved ? `<cause>${across ? 'VERSION_CHANGE' : 'FULL_RUN'}</cause>` : '',
    '<previous_run_id>40</previous_run_id>',
    '<new_run_id>42</new_run_id>',
    `<previous_model_version_id>3</previous_model_version_id>`,
    `<new_model_version_id>${across ? 4 : 3}</new_model_version_id>`,
    `<methodological>${across}</methodological>`,
    ...variables.map((v) => `<responsible_variables>${v}</responsible_variables>`),
    `<rank_only>${rankOnly}</rank_only>`,
    '</SegmentMigration>',
  ].join('');
}

/**
 * The page of customers the service would return for these filters: the members of one cell, or the
 * entries or exits of one label, which are the cells that cross into or out of it.
 */
export function membersXml(kind: Kind, q: URLSearchParams): string {
  const xml = matrixXml(kind);
  const cells = cellsOf(xml);
  const direction = q.get('direction');
  const code = q.get('label_code');
  const wanted =
    direction === null
      ? cells.filter(
          (c) =>
            same(c.previous, q.get('previous_state'), q.get('previous_label_code')) &&
            same(c.next, q.get('new_state'), q.get('new_label_code')),
        )
      : cells.filter(
          (c) =>
            (direction === 'entries' ? same(c.next, 'label', code) : same(c.previous, 'label', code)) &&
            !(c.previous.state === c.next.state && c.previous.code === c.next.code),
        );
  const filter = q.get('methodological');
  const keep = filter === null || (filter === 'true') === (kind === 'across');
  const all: string[] = [];
  let serial = 1;
  for (const cell of keep ? wanted : [])
    for (let i = 0; i < cell.count; i += 1) {
      all.push(migration(kind, cell, i, serial));
      serial += 1;
    }
  const page = Number(q.get('page') ?? '1');
  const size = Number(q.get('size') ?? '20');
  const items = all.slice((page - 1) * size, page * size).join('');
  return `<SegmentMigrations xmlns="${SEGMENTS_NS}" xmlns:common="${COMMON_NS}"><from>${escapeXml(q.get('from') ?? '')}</from><to>${escapeXml(q.get('to') ?? '')}</to>${items}<common:PageInfo><common:page>${page}</common:page><common:size>${size}</common:size><common:total>${all.length}</common:total></common:PageInfo></SegmentMigrations>`;
}

/** A recorded explanation, for the customer it is about or, for any other, the kind's own. */
export function explanationXml(kind: Kind, customerId: string): string {
  const name =
    customerId === '0c9f6a3e-5d2b-4c8e-9a41-7e3b2d1f8a66'
      ? 'migration-explanation-new-customer'
      : kind === 'within'
        ? 'migration-explanation'
        : 'migration-explanation-rank-only';
  return example(name).replace(
    /<customer_id>[^<]*<\/customer_id>/,
    `<customer_id>${escapeXml(customerId)}</customer_id>`,
  );
}
