import type { ErrorView, FieldDetail, PageView, Result } from '../shared/api';
import { localError, plain, type Backoffice } from './backoffice';
import type { CallResult } from './transport';

// Checks for every value a screen sends, shared by the feature classes
// (Catalog, Floor). A value that fails is refused here and nothing is sent.

export const MAX_INT = 2147483647;
const PRICE = /^(0|[1-9][0-9]{0,7})\.[0-9]{2}$/;
const UUID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/;

/** A value from the screens that cannot be sent. Nothing reaches the network. */
export class Refused extends Error {
  constructor(readonly view: ErrorView) {
    super(view.code);
  }
}

export function invalidInput(message: string): Refused {
  return new Refused(localError('invalid_input', message));
}

export function id(value: unknown, max: number, label: string): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1 || value > max)
    throw invalidInput(`${label} must be a whole number from 1 to ${max}.`);
  return value;
}

export function pageNumber(value: unknown): number {
  if (value === undefined) return 1;
  return id(value, MAX_INT, 'The page');
}

export function pageSize(value: unknown): number {
  if (value === undefined) return 20;
  return id(value, 100, 'The page size');
}

export function optionalText(value: unknown, label: string): string | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== 'string') throw invalidInput(`${label} must be text.`);
  return value;
}

export function record(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value))
    throw invalidInput(`${label} is missing.`);
  return value as Record<string, unknown>;
}

/** Field checks mirroring the XSD, so a refusal names its field the way the service would. */
export class FieldChecks {
  readonly details: FieldDetail[] = [];

  text(field: string, value: unknown, max: number): string {
    if (typeof value !== 'string' || value.trim() === '') {
      this.details.push({ field, code: 'required' });
      return '';
    }
    if (value.length > max) this.details.push({ field, code: 'too_long' });
    return value;
  }

  id(field: string, value: unknown, max: number): number {
    if (typeof value !== 'number' || !Number.isInteger(value) || value < 1 || value > max) {
      this.details.push({ field, code: 'out_of_range' });
      return 0;
    }
    return value;
  }

  price(field: string, value: unknown): string {
    if (typeof value !== 'string' || !PRICE.test(value)) {
      this.details.push({ field, code: 'invalid_format' });
      return '';
    }
    return value;
  }

  done(): void {
    if (this.details.length > 0)
      throw new Refused(
        localError('invalid_body', 'Some values cannot be sent. Correct the marked fields.', this.details),
      );
  }
}

export function pageOf(data: Record<string, unknown>): PageView {
  const p = data.PageInfo as { page: string; size: string; total: string };
  return { page: Number(p.page), size: Number(p.size), total: Number(p.total) };
}

export function failed<T>(res: CallResult): Result<T> {
  if (res.ok) throw new Error('a refusal was expected');
  return { ok: false, error: plain(res.error) };
}

export function optionalUuid(value: unknown, label: string): string | undefined {
  if (value === undefined || value === '') return undefined;
  if (typeof value !== 'string' || !UUID.test(value)) throw invalidInput(`${label} must be a UUID.`);
  return value;
}

export function uuid(value: unknown, label: string): string {
  const v = optionalUuid(value, label);
  if (v === undefined) throw invalidInput(`${label} must be a UUID.`);
  return v;
}

export function optionalDate(value: unknown, label: string): string | undefined {
  if (value === undefined || value === '') return undefined;
  if (typeof value !== 'string' || !DATE.test(value) || Number.isNaN(Date.parse(value)))
    throw invalidInput(`${label} must be a date (YYYY-MM-DD).`);
  return value;
}

export function optionalDateTime(value: unknown, label: string): string | undefined {
  if (value === undefined || value === '') return undefined;
  if (typeof value !== 'string' || !DATE_TIME.test(value) || Number.isNaN(Date.parse(value)))
    throw invalidInput(`${label} must be a date and time with its offset (RFC 3339).`);
  return value;
}

export function optionalBoolean(value: unknown, label: string): boolean | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== 'boolean') throw invalidInput(`${label} must be yes or no.`);
  return value;
}

export function optionalId(value: unknown, max: number, label: string): number | undefined {
  if (value === undefined) return undefined;
  return id(value, max, label);
}

/** Runs work through the session, turning a local refusal into its error view. */
export function checked<T>(office: Backoffice, work: () => Promise<Result<T>>): Promise<Result<T>> {
  return office.attempt(async () => {
    try {
      return await work();
    } catch (e) {
      if (e instanceof Refused) return { ok: false, error: e.view };
      throw e;
    }
  });
}
