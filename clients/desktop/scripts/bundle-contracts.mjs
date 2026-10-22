// Copies each registered service's XSD, plus common/v1, into bundled/ and
// extracts every operation's x-mosaiq-roles into bundled/operations.json.
// The output is generated and git-ignored; never edit it by hand (F16-01).
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync, copyFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'yaml';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const contracts = process.env.MOSAIQ_CONTRACTS_DIR ?? resolve(root, '../../contracts');
const out = process.env.MOSAIQ_BUNDLE_DIR ?? join(root, 'bundled');
const NS = 'https://mosaiq.maxthecoder.online/xml';
const METHODS = ['get', 'post', 'put', 'patch', 'delete'];

function fail(message) {
  console.error(`bundle-contracts: ${message}`);
  process.exit(1);
}

const registry = parse(readFileSync(join(contracts, 'registry.yaml'), 'utf8'));
rmSync(out, { recursive: true, force: true });
mkdirSync(join(out, 'schemas', 'common', 'v1'), { recursive: true });

const commonSrc = join(contracts, 'common', 'v1', 'common.xsd');
if (!existsSync(commonSrc)) fail('contracts/common/v1/common.xsd is missing');
copyFileSync(commonSrc, join(out, 'schemas', 'common', 'v1', 'common.xsd'));

const schemas = { [`${NS}/common/v1`]: 'common/v1/common.xsd' };
const services = [];
const skipped = [];
const operations = [];

for (const svc of registry.services) {
  const name = svc.name;
  const openapi = join(contracts, name, 'openapi.yaml');
  const xsd = join(contracts, name, `${name}.xsd`);
  if (!existsSync(openapi)) {
    skipped.push(name);
    continue;
  }
  if (!existsSync(xsd)) fail(`${name} has an openapi.yaml but no ${name}.xsd`);
  const text = readFileSync(xsd, 'utf8');
  const namespace = `${NS}/${name}/v1`;
  if (!text.includes(`targetNamespace="${namespace}"`)) {
    fail(`${name}.xsd does not declare targetNamespace ${namespace}`);
  }
  mkdirSync(join(out, 'schemas', name), { recursive: true });
  copyFileSync(xsd, join(out, 'schemas', name, `${name}.xsd`));
  schemas[namespace] = `${name}/${name}.xsd`;
  services.push({ name, resources: svc.resources });

  const doc = parse(readFileSync(openapi, 'utf8'));
  for (const [path, item] of Object.entries(doc.paths ?? {})) {
    for (const method of METHODS) {
      const op = item[method];
      if (!op) continue;
      operations.push({
        service: name,
        operationId: op.operationId,
        method: method.toUpperCase(),
        path,
        roles: Array.isArray(op['x-mosaiq-roles']) ? op['x-mosaiq-roles'] : null,
      });
    }
  }
}

writeFileSync(
  join(out, 'operations.json'),
  `${JSON.stringify({ namespaceBase: NS, schemas, services, skipped, operations }, null, 2)}\n`,
);
console.log(`bundle-contracts: ${services.length} services bundled, ${operations.length} operations`);
if (skipped.length > 0) {
  console.log(`bundle-contracts: skipped (contract not merged yet): ${skipped.join(', ')}`);
}
