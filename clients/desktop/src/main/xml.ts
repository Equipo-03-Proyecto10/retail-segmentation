import { XMLBuilder, XMLParser } from 'fast-xml-parser';
import { validateXML } from 'xmllint-wasm';
import type { Bundle } from './bundle';

export interface RootInfo {
  local: string;
  namespace: string | null;
}

const COMMON_FILE = 'common/v1/common.xsd';

/**
 * Reads the root element's namespace without parsing the document. Returns
 * null for anything that is not a plain XML document (DTDs are refused).
 */
export function rootElement(xml: string): RootInfo | null {
  if (/<!DOCTYPE|<!ENTITY/i.test(xml)) return null;
  // Repeated until nothing changes: one pass over `<!-<!---->-` leaves `<!--`.
  let stripped = xml.charCodeAt(0) === 0xfeff ? xml.slice(1) : xml;
  let previous: string;
  do {
    previous = stripped;
    stripped = stripped.replace(/<\?[\s\S]*?\?>/g, '').replace(/<!--[\s\S]*?-->/g, '');
  } while (stripped !== previous);
  const match = /^\s*<([A-Za-z_][\w.-]*)(?::([A-Za-z_][\w.-]*))?((?:\s+[^<>]*?)?)\s*\/?>/.exec(stripped);
  if (!match) return null;
  const [, first, second, attrs] = match;
  const prefix = second === undefined ? null : first;
  const local = second ?? first;
  const wanted = prefix === null ? 'xmlns' : `xmlns:${prefix}`;
  let namespace: string | null = null;
  for (const a of (attrs ?? '').matchAll(/([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)) {
    if (a[1] === wanted) namespace = a[2] ?? a[3] ?? null;
  }
  return { local: local as string, namespace };
}

export class XsdValidator {
  constructor(private readonly bundle: Bundle) {}

  /** Validates a document against the schema bundled for its namespace. */
  async validate(xml: string, namespace: string): Promise<{ valid: boolean; errors: string[] }> {
    const file = this.bundle.schemas[namespace];
    if (file === undefined) return { valid: false, errors: [`no bundled schema for ${namespace}`] };
    const schema = [{ fileName: file, contents: this.bundle.files[file] as string }];
    const preload =
      file === COMMON_FILE
        ? []
        : [{ fileName: COMMON_FILE, contents: this.bundle.files[COMMON_FILE] as string }];
    const result = await validateXML({
      xml: [{ fileName: 'document.xml', contents: xml }],
      schema,
      preload,
    });
    return { valid: result.valid, errors: result.errors.map((e) => e.message) };
  }
}

const REPEATED = new Set([
  'Products.Product',
  'Categories.Category',
  'StockLevels.StockLevel',
  'Sales.Sale',
  'Channels.Channel',
  'EngagementEvents.EngagementEvent',
  'CheckIns.CheckIn',
  'Stores.Store',
  'Users.User',
  'Roles.Role',
]);
/** Child elements that repeat wherever they appear: error details, sale lines, summary groups, event history, check-in reviews. */
const REPEATED_CHILDREN = new Set(['details', 'lines', 'groups', 'history', 'reviews']);

const parser = new XMLParser({
  removeNSPrefix: true,
  parseTagValue: false,
  parseAttributeValue: false,
  ignoreAttributes: true,
  processEntities: false,
  // Repeated elements are arrays even when a page holds one. Matched by path,
  // so a single <Product> root stays an object.
  isArray: (name, jpath) => REPEATED_CHILDREN.has(name) || REPEATED.has(String(jpath)),
});

/** Parses a document that already passed the namespace and XSD checks. */
export function parseXml(xml: string): Record<string, unknown> {
  return parser.parse(xml) as Record<string, unknown>;
}

const builder = new XMLBuilder({
  format: false,
  suppressEmptyNode: true,
  suppressBooleanAttributes: false,
  ignoreAttributes: false,
  attributeNamePrefix: '@_',
  processEntities: true,
});

const XSI = 'http://www.w3.org/2001/XMLSchema-instance';

/** A request field: text, or null for an explicit xsi:nil (for example, make a category a root). */
export type FieldValue = string | null;

/** Builds a request body; scalar fields only, in the given order. */
export function buildXml(root: string, namespace: string, fields: Record<string, FieldValue>): string {
  const element: Record<string, unknown> = { '@_xmlns': namespace };
  let nil = false;
  for (const [name, value] of Object.entries(fields)) {
    if (value === null) {
      element[name] = { '@_xsi:nil': 'true' };
      nil = true;
    } else {
      element[name] = value;
    }
  }
  if (nil) element['@_xmlns:xsi'] = XSI;
  const body = builder.build({ [root]: element }) as string;
  return `<?xml version="1.0" encoding="UTF-8"?>${body}`;
}
