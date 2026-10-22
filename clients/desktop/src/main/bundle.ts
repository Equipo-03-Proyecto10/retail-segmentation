import { readFileSync } from 'node:fs';
import { join } from 'node:path';

export interface Operation {
  service: string;
  operationId: string;
  method: string;
  path: string;
  /** null when the operation declares no x-mosaiq-roles (public). */
  roles: string[] | null;
}

export interface Bundle {
  namespaceBase: string;
  /** namespace -> path of the XSD under schemas/ */
  schemas: Record<string, string>;
  services: { name: string; resources: string[] }[];
  skipped: string[];
  operations: Operation[];
  /** schema file name -> contents, for every bundled XSD. */
  files: Record<string, string>;
}

export function loadBundle(dir: string): Bundle {
  const meta = JSON.parse(readFileSync(join(dir, 'operations.json'), 'utf8')) as Omit<Bundle, 'files'>;
  const files: Record<string, string> = {};
  for (const file of Object.values(meta.schemas)) {
    files[file] = readFileSync(join(dir, 'schemas', file), 'utf8');
  }
  return { ...meta, files };
}

export function serviceNamespace(bundle: Bundle, service: string): string {
  return `${bundle.namespaceBase}/${service}/v1`;
}

export const COMMON_NAMESPACE_SUFFIX = 'common/v1';

export function commonNamespace(bundle: Bundle): string {
  return `${bundle.namespaceBase}/${COMMON_NAMESPACE_SUFFIX}`;
}

/** Service that owns a top-level resource, per contracts/registry.yaml. */
export function serviceForResource(bundle: Bundle, resource: string): string | undefined {
  return bundle.services.find((s) => s.resources.includes(resource))?.name;
}

/** Services that have at least one operation the role may call. */
export function servicesForRole(bundle: Bundle, role: string): { name: string; resources: string[] }[] {
  return bundle.services.filter((s) =>
    bundle.operations.some((o) => o.service === s.name && o.roles?.includes(role)),
  );
}
