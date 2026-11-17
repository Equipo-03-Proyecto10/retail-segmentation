import type {
  MigrationCellView,
  MigrationDirection,
  MigrationExplanationView,
  MigrationListView,
  MigrationMatrixView,
  MigrationMeasureView,
  MigrationState,
  MigrationTotalView,
  MigrationView,
  MigrationWindow,
  Result,
} from '../shared/api';
import { MIGRATION_OPERATIONS } from '../shared/modules';
import { localError, type Backoffice } from './backoffice';
import {
  checked,
  failed,
  invalidInput,
  optionalBoolean,
  optionalDateTime,
  optionalText,
  pageNumber,
  pageOf,
  pageSize,
  record,
  uuid,
} from './checks';

// The migration matrix (F28-06, #875): segments-service's matrix between two instants, the customers
// behind a cell or a label's entries and exits, and one customer's explanation. The service decides
// every classification (the state of each side, the movement, `methodological`, the responsible
// variables); this class only reads them through the transport (XML, XSD-validated) and checks
// the values the screen sends. A comparison across model versions comes back marked methodological
// and is passed on marked, never as behaviour (ADR-0064). The service operations are published
// ahead of their implementation (ADR-0073); a service that does not answer yet is shown as it
// answers.

const STATES: readonly string[] = ['label', 'unassigned', 'new'];
const DIRECTIONS: readonly string[] = ['entries', 'exits'];
const LABEL_MAX = 64;

interface TotalXml {
  state: string;
  label_code?: string;
  before_count: string;
  after_count: string;
  entries: string;
  exits: string;
}

interface CellXml {
  previous_state: string;
  previous_label_code?: string;
  new_state: string;
  new_label_code?: string;
  movement: string;
  customer_count: string;
}

interface MatrixXml {
  from: string;
  to: string;
  from_model_version_id?: string;
  to_model_version_id?: string;
  methodological: string;
  customer_count: string;
  MigrationTotal?: TotalXml[];
  MigrationCell?: CellXml[];
}

interface MigrationXml {
  customer_id: string;
  previous_state: string;
  previous_label_code?: string;
  new_state: string;
  new_label_code?: string;
  movement: string;
  changed_at?: string;
  cause?: string;
  previous_run_id?: string;
  new_run_id?: string;
  previous_model_version_id?: string;
  new_model_version_id?: string;
  methodological: string;
  responsible_variables?: string[];
  rank_only: string;
}

interface MeasureXml {
  variable: string;
  unit: string;
  previous_value?: string;
  new_value?: string;
  judgement: string;
  not_compared_reason?: string;
  rank_changed: string;
  statement: string;
}

const isTrue = (v: string): boolean => v === 'true' || v === '1';

const number = (v: string | undefined): number | undefined => (v === undefined ? undefined : Number(v));

/** Present only when the service sent it: a field it left out stays absent. */
function present<T extends object>(view: T): T {
  return Object.fromEntries(Object.entries(view).filter(([, v]) => v !== undefined)) as T;
}

function totalOf(x: TotalXml): MigrationTotalView {
  return present({
    state: x.state as MigrationState,
    label_code: x.label_code,
    before_count: Number(x.before_count),
    after_count: Number(x.after_count),
    entries: Number(x.entries),
    exits: Number(x.exits),
  });
}

function cellOf(x: CellXml): MigrationCellView {
  return present({
    previous_state: x.previous_state as MigrationState,
    previous_label_code: x.previous_label_code,
    new_state: x.new_state as MigrationState,
    new_label_code: x.new_label_code,
    movement: x.movement as MigrationCellView['movement'],
    customer_count: Number(x.customer_count),
  });
}

function migrationOf(x: MigrationXml): MigrationView {
  return present({
    customer_id: x.customer_id,
    previous_state: x.previous_state as MigrationState,
    previous_label_code: x.previous_label_code,
    new_state: x.new_state as MigrationState,
    new_label_code: x.new_label_code,
    movement: x.movement as MigrationView['movement'],
    changed_at: x.changed_at,
    cause: x.cause,
    previous_run_id: number(x.previous_run_id),
    new_run_id: number(x.new_run_id),
    previous_model_version_id: number(x.previous_model_version_id),
    new_model_version_id: number(x.new_model_version_id),
    methodological: isTrue(x.methodological),
    responsible_variables: x.responsible_variables ?? [],
    rank_only: isTrue(x.rank_only),
  });
}

function measureOf(x: MeasureXml): MigrationMeasureView {
  return present({
    variable: x.variable,
    unit: x.unit,
    previous_value: x.previous_value,
    new_value: x.new_value,
    judgement: x.judgement as MigrationMeasureView['judgement'],
    not_compared_reason: x.not_compared_reason,
    rank_changed: isTrue(x.rank_changed),
    statement: x.statement,
  });
}

function instant(value: unknown, label: string): string {
  const v = optionalDateTime(value, label);
  if (v === undefined) throw invalidInput(`${label} is required.`);
  return v;
}

/** The earlier instant is the before state whatever order the two were chosen in (RN-59). */
function window(raw: Record<string, unknown>): MigrationWindow {
  const a = instant(raw.from, 'The first date');
  const b = instant(raw.to, 'The second date');
  const [ta, tb] = [Date.parse(a), Date.parse(b)];
  if (ta === tb) throw invalidInput('Choose two different dates: a migration compares two moments.');
  return ta < tb ? { from: a, to: b } : { from: b, to: a };
}

function enumOf<T extends string>(value: unknown, allowed: readonly string[], label: string): T {
  if (typeof value !== 'string' || !allowed.includes(value)) throw invalidInput(`${label} is not known.`);
  return value as T;
}

function labelOf(value: unknown, label: string): string {
  const v = optionalText(value, label);
  if (v === undefined || v.trim() === '' || v.length > LABEL_MAX)
    throw invalidInput(`${label} must be 1 to ${LABEL_MAX} characters.`);
  return v;
}

/** One side of a cell: its state, and a label code exactly when the state is label. */
function side(
  q: Record<string, unknown>,
  stateKey: string,
  codeKey: string,
  label: string,
): { state: MigrationState; code?: string } {
  const state = enumOf<MigrationState>(q[stateKey], STATES, `The ${label} state`);
  if (state === 'label') return { state, code: labelOf(q[codeKey], `The ${label} label`) };
  if (q[codeKey] !== undefined) throw invalidInput(`A ${label} label goes only with the state label.`);
  return { state };
}

export class Migrations {
  constructor(private readonly office: Backoffice) {}

  /** A role reads a call only when the contract's x-mosaiq-roles allow its operation. */
  private allowed(operation: string): Result<never> | null {
    const state = this.office.state();
    if (state.status !== 'signed_in')
      return { ok: false, error: localError('signed_out', 'You are signed out.') };
    return state.operations.includes(operation)
      ? null
      : { ok: false, error: localError('forbidden_role', 'Your role may not read migrations.') };
  }

  matrix(query: unknown): Promise<Result<MigrationMatrixView>> {
    return checked(this.office, async () => {
      const q = record(query, 'The comparison');
      const refused = this.allowed(MIGRATION_OPERATIONS.matrix);
      if (refused !== null) return refused;
      const w = window(q);
      const params = new URLSearchParams({ from: w.from, to: w.to });
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/segment-migrations/matrix?${params.toString()}`,
      });
      if (!res.ok) return failed(res);
      const x = res.data?.SegmentMigrationMatrix as MatrixXml;
      return {
        ok: true,
        value: present({
          from: x.from,
          to: x.to,
          from_model_version_id: number(x.from_model_version_id),
          to_model_version_id: number(x.to_model_version_id),
          methodological: isTrue(x.methodological),
          customer_count: Number(x.customer_count),
          totals: (x.MigrationTotal ?? []).map(totalOf),
          cells: (x.MigrationCell ?? []).map(cellOf),
        }),
      };
    });
  }

  /** The customers behind one cell, or behind one label's entries or exits. */
  list(query: unknown): Promise<Result<MigrationListView>> {
    return checked(this.office, async () => {
      const q = record(query, 'The query');
      const refused = this.allowed(MIGRATION_OPERATIONS.list);
      if (refused !== null) return refused;
      const w = window(q);
      const params = new URLSearchParams({ from: w.from, to: w.to });
      const isCell = q.previous_state !== undefined || q.new_state !== undefined;
      const isDirection = q.direction !== undefined || q.label_code !== undefined;
      if (isCell === isDirection)
        throw invalidInput('Open one cell, or the entries or exits of one label, but not both.');
      if (isCell) {
        const before = side(q, 'previous_state', 'previous_label_code', 'before');
        const after = side(q, 'new_state', 'new_label_code', 'after');
        params.set('previous_state', before.state);
        if (before.code !== undefined) params.set('previous_label_code', before.code);
        params.set('new_state', after.state);
        if (after.code !== undefined) params.set('new_label_code', after.code);
      } else {
        params.set('direction', enumOf<MigrationDirection>(q.direction, DIRECTIONS, 'The direction'));
        params.set('label_code', labelOf(q.label_code, 'The label'));
      }
      const methodological = optionalBoolean(q.methodological, 'The methodological filter');
      if (methodological !== undefined) params.set('methodological', String(methodological));
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/segment-migrations?${params.toString()}`,
      });
      if (!res.ok) return failed(res);
      const data = res.data?.SegmentMigrations as {
        from: string;
        to: string;
        SegmentMigration?: MigrationXml[];
      } & Record<string, unknown>;
      return {
        ok: true,
        value: {
          from: data.from,
          to: data.to,
          items: (data.SegmentMigration ?? []).map(migrationOf),
          page: pageOf(data),
        },
      };
    });
  }

  /** One customer's migration with the raw values and the judgement of each variable (RN-60). */
  explain(query: unknown): Promise<Result<MigrationExplanationView>> {
    return checked(this.office, async () => {
      const q = record(query, 'The query');
      const refused = this.allowed(MIGRATION_OPERATIONS.explanation);
      if (refused !== null) return refused;
      const customer = uuid(q.customer_id, 'The customer');
      const w = window(q);
      const params = new URLSearchParams({ customer_id: customer, from: w.from, to: w.to });
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/segment-migrations/explanation?${params.toString()}`,
      });
      if (!res.ok) return failed(res);
      const x = res.data?.SegmentMigrationExplanation as {
        from: string;
        to: string;
        migration: MigrationXml;
        MigrationMeasure?: MeasureXml[];
      };
      return {
        ok: true,
        value: {
          from: x.from,
          to: x.to,
          migration: migrationOf(x.migration),
          measures: (x.MigrationMeasure ?? []).map(measureOf),
        },
      };
    });
  }
}
