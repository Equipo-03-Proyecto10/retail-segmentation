import { randomUUID } from 'node:crypto';
import type { ErrorView } from '../shared/api';
import { commonNamespace, serviceForResource, serviceNamespace, type Bundle } from './bundle';
import { buildXml, parseXml, rootElement, type FieldValue, type XsdValidator } from './xml';

export type FetchLike = (url: string, init: RequestInit) => Promise<Response>;

export interface CallOptions {
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  /** Path starting with /v1/<resource>; the service is derived from the registry. */
  path: string;
  /** Root element and fields of the XML request body, when there is one. */
  body?: { root: string; fields: Record<string, FieldValue> };
  bearer?: string;
}

export type CallResult =
  | {
      ok: true;
      status: number;
      service: string;
      correlationId: string;
      root?: string;
      data?: Record<string, unknown>;
    }
  | { ok: false; status: number; service: string; correlationId: string; error: ErrorView };

/** A response that broke its contract. It carries no part of the response. */
export class ContractDefect extends Error {
  constructor(
    readonly service: string,
    readonly correlationId: string,
    readonly reason: string,
  ) {
    super(`contract defect: ${service} (${correlationId}): ${reason}`);
  }
}

/** A request body that fails the service's own XSD. Nothing was sent. */
export class InvalidRequest extends Error {
  constructor(
    readonly service: string,
    readonly errors: string[],
  ) {
    super(`request body for ${service} fails its schema: ${errors.join('; ')}`);
  }
}

export function defectView(defect: ContractDefect): ErrorView {
  return {
    code: 'contract_defect',
    message: `The ${defect.service} service answered outside its contract. Nothing of the response was used.`,
    correlation_id: defect.correlationId,
    details: [],
  };
}

export class Transport {
  constructor(
    private readonly bundle: Bundle,
    private readonly validator: XsdValidator,
    private readonly baseUrl: () => string,
    private readonly fetchImpl: FetchLike = (url, init) => fetch(url, init),
  ) {}

  serviceOf(path: string): string {
    const resource = /^\/v1\/([^/?#]+)/.exec(path)?.[1];
    if (resource === undefined) throw new Error(`not a /v1 path: ${path}`);
    // The one JSON-only operation is never called by this client (E-04).
    if (resource === 'jwks.json') throw new Error('jwks.json is not called by the desktop client');
    const service = serviceForResource(this.bundle, resource);
    if (service === undefined) throw new Error(`no registered service for resource ${resource}`);
    return service;
  }

  async call(options: CallOptions): Promise<CallResult> {
    const service = this.serviceOf(options.path);
    const ns = serviceNamespace(this.bundle, service);
    const correlationId = randomUUID();
    const headers: Record<string, string> = { Accept: 'application/xml', 'X-Correlation-ID': correlationId };
    let body: string | undefined;
    if (options.body) {
      body = buildXml(options.body.root, ns, options.body.fields);
      const check = await this.validator.validate(body, ns);
      if (!check.valid) throw new InvalidRequest(service, check.errors);
      headers['Content-Type'] = 'application/xml';
    }
    if (options.bearer !== undefined) headers.Authorization = `Bearer ${options.bearer}`;

    const url = `${this.baseUrl()}/api/${service}${options.path}`;
    const response = await this.fetchImpl(url, { method: options.method, headers, body, redirect: 'error' });
    const text = await response.text();
    const status = response.status;
    const ok = status >= 200 && status < 300;

    if (status === 204 && text.length === 0) return { ok: true, status, service, correlationId };

    const defect = (reason: string): ContractDefect => new ContractDefect(service, correlationId, reason);
    const root = rootElement(text);
    if (root === null) throw defect('the body is not an XML document');
    const expected = ok ? ns : commonNamespace(this.bundle);
    if (root.namespace !== expected)
      throw defect(`root namespace ${root.namespace ?? '(none)'} is not ${expected}`);
    const checked = await this.validator.validate(text, expected);
    if (!checked.valid) throw defect(`schema validation failed: ${checked.errors[0] ?? 'unknown'}`);

    const data = parseXml(text);
    if (ok) return { ok: true, status, service, correlationId, root: root.local, data };
    const e = data.Error as {
      code: string;
      message: string;
      correlation_id: string;
      details?: FieldDetailXml[];
    };
    const error: ErrorView = {
      code: e.code,
      message: e.message,
      correlation_id: e.correlation_id,
      details: (e.details ?? []).map((d) => ({ field: d.field, code: d.code })),
    };
    const retryAfter = response.headers.get('Retry-After');
    if (retryAfter !== null) error.retry_after = retryAfter;
    return { ok: false, status, service, correlationId, error };
  }
}

interface FieldDetailXml {
  field: string;
  code: string;
}
