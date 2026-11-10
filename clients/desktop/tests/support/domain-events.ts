import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// The domain events the audit-trail contract records as examples (contracts/audit-trail/examples/),
// read from disk so no test retypes one. The nine single-event examples are the nine rows the
// contract's own page says exist (`total` 9). The contract has no example of a campaign activation,
// the decision the "Campaign decisions" shortcut looks for, so one more event is made from its
// critical-action example by changing only the values that say what was done; it is a valid
// document of the same XSD, which every test that reads it proves.

const DIR = join(__dirname, '..', '..', '..', '..', 'contracts', 'audit-trail', 'examples');
const NS = 'https://mosaiq.maxthecoder.online/xml/audit-trail/v1';
const COMMON = 'https://mosaiq.maxthecoder.online/xml/common/v1';

export const EVENT_EXAMPLES = [
  'domain-event-calculation-run',
  'domain-event-model-published',
  'domain-event-model-rolled-back',
  'domain-event-permission-changed',
  'domain-event-stores-assigned',
  'domain-event-critical-action',
  'domain-event-history-read',
  'domain-event-history-read-refused',
  'domain-event-audit-read',
] as const;

export const read = (name: string): string => readFileSync(join(DIR, `${name}.xml`), 'utf8');

export const CAMPAIGN_EVENT = 'campaign-activated';

/** The contract's critical-action example, turned into the activation of campaign 12 by a marketer. */
function campaignActivated(): string {
  const swaps: [string, string][] = [
    ['7a0c5e1d-3b2f-4c8a-9d61-000000000006', '7a0c5e1d-3b2f-4c8a-9d61-000000000010'],
    ['2026-11-07T17:21:30.002Z', '2026-11-07T08:30:55.401Z'],
    ['2026-11-07T17:21:30.015Z', '2026-11-07T08:30:55.418Z'],
    ['11111111-1111-1111-1111-000000000003', '11111111-1111-1111-1111-000000000004'],
    ['STORE_MANAGER', 'MARKETING'],
    ['svc_sales', 'svc_campaigns'],
    ['  <store_id>4</store_id>\n', ''],
    ['sale_reversal', 'campaign'],
    ['<subject_id>77</subject_id>', '<subject_id>12</subject_id>'],
    ['corr-reversal-77', 'corr-activate-12'],
    ['SALE_REVERSED', 'CAMPAIGN_ACTIVATED'],
    ['CUSTOMER_RETURN', 'PLANNED_LAUNCH'],
  ];
  let text = read('domain-event-critical-action');
  for (const [from, to] of swaps) {
    if (!text.includes(from)) throw new Error(`the critical-action example no longer has ${from}`);
    text = text.replace(from, to);
  }
  return text;
}

/** The text of the first element of that name, or an empty string. */
export const field = (block: string, name: string): string =>
  new RegExp(`<${name}>([^<]*)</${name}>`).exec(block)?.[1] ?? '';

export interface RecordedEvent {
  name: string;
  /** The whole document, as the service answers one event. */
  document: string;
  /** The DomainEvent element alone, for a page that declares the namespace once. */
  element: string;
  id: string;
  kind: string;
  recordedAt: string;
  subjectType: string;
}

/** The recorded events, newest recorded first, as the service orders them. */
export function recordedEvents(): RecordedEvent[] {
  const documents: [string, string][] = [
    ...EVENT_EXAMPLES.map((name): [string, string] => [name, read(name)]),
    [CAMPAIGN_EVENT, campaignActivated()],
  ];
  return documents
    .map(([name, document]) => ({
      name,
      document,
      element: document.replace(/^<\?xml[^>]*\?>\s*/, '').replace(` xmlns="${NS}"`, ''),
      id: field(document, 'event_id'),
      kind: field(document, 'kind'),
      recordedAt: field(document, 'recorded_at'),
      subjectType: field(document, 'subject_type'),
    }))
    .sort((a, b) => Date.parse(b.recordedAt) - Date.parse(a.recordedAt));
}

/** A page of DomainEvent elements, with the PageInfo the contract's page carries. */
export function eventsPage(list: readonly string[], size = 20): string {
  return `<DomainEvents xmlns="${NS}" xmlns:common="${COMMON}">${list.join('')}<common:PageInfo><common:page>1</common:page><common:size>${size}</common:size><common:total>${list.length}</common:total></common:PageInfo></DomainEvents>`;
}
