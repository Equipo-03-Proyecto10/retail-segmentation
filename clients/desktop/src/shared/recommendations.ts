import type { AvailabilityView, FactorsView, InspectedItemView, InspectedReasonView, NamedView } from './api';

// Words for what recommendations-service decided (F28-08, #877; ADR-0067, RN-40). The service
// decides every status, kind, exclusion and figure; this file only says them in plain words and
// never derives one. A value it does not know is printed as the service sent it.

export const STATUSES: readonly string[] = [
  'recommended',
  'none_match',
  'no_open_assignment',
  'unassigned_result',
  'no_usual_store',
  'PERSONALIZATION_WITHDRAWN',
];

const STATUS_TEXT: Record<string, string> = {
  recommended: 'At least one product is available here and matches the customer.',
  none_match:
    'No product qualified as a recommendation here. Alternatives or products held by another store may still be listed.',
  no_open_assignment: 'The customer has no open segment, so nothing was matched.',
  unassigned_result:
    'The latest segmentation run found nothing to label the customer with, so nothing was matched.',
  no_usual_store:
    'No store was named and the customer has no usual store, so availability could not be judged. No store is guessed.',
  PERSONALIZATION_WITHDRAWN:
    'The customer objected to personalization. Nothing about them was read and nothing was recommended.',
};

const STATUS_LABEL: Record<string, string> = {
  recommended: 'Recommended',
  none_match: 'No match',
  no_open_assignment: 'No open segment',
  unassigned_result: 'Unassigned result',
  no_usual_store: 'No usual store',
  PERSONALIZATION_WITHDRAWN: 'Personalization withdrawn',
};

export const statusLabel = (status: string): string => STATUS_LABEL[status] ?? status;

export const statusText = (status: string): string =>
  STATUS_TEXT[status] ?? `The service answered with the status ${status}.`;

const KIND_LABEL: Record<string, string> = {
  primary: 'Recommended',
  alternative: 'Alternative',
  elsewhere: 'Another store',
};

export const kindLabel = (kind: string): string => KIND_LABEL[kind] ?? kind;

const UNAVAILABLE_TEXT: Record<string, string> = {
  not_sellable: 'its commercial status does not allow the sale',
  channel_not_enabled: 'the store does not enable the channel',
  out_of_stock: 'no unit is on hand',
};

export const unavailableText = (reason: string): string => UNAVAILABLE_TEXT[reason] ?? reason;

/** The exclusion reasons in the order the service applies them (ADR-0067). */
const EXCLUSION_TEXT: Record<string, string> = {
  already_bought: 'The customer bought it in the window.',
  inactive: 'The product is not active.',
  not_sellable: 'Its commercial status does not allow the sale, such as discontinued.',
  store_not_selling: "The reference store's status does not permit sales.",
  channel_not_enabled: 'The reference channel is not enabled at the store.',
  out_of_stock: 'No unit is on hand at the reference store.',
  PERSONALIZATION_WITHDRAWN: "The customer's consent or restrictions forbid it.",
};

export const exclusionText = (reason: string): string => EXCLUSION_TEXT[reason] ?? reason;

/** A name with its id, or the id alone when the owner of the name could not be read. */
export function named(ref: NamedView, noun: string): string {
  return ref.name === undefined ? `${noun} #${ref.id} (name unavailable)` : `${ref.name} (#${ref.id})`;
}

/** A decimal share ("0.4") as the service sent it, with the percentage it stands for. */
export function shareText(share: string): string {
  const n = Number(share);
  if (!Number.isFinite(n)) return share;
  return `${share} (${(n * 100).toFixed(1).replace(/\.0$/, '')}%)`;
}

export function factorsText(f: FactorsView | undefined): string {
  if (f === undefined) return 'Not scored: an alternative is a declared substitute.';
  const parts = [`${f.signals_matched} of 3 signals matched`];
  if (f.segment_buyers !== undefined) parts.push(`${f.segment_buyers} segment buyers`);
  if (f.category_share !== undefined) parts.push(`category share ${shareText(f.category_share)}`);
  return parts.join('; ');
}

export function reasonText(r: InspectedReasonView): string {
  const category = r.category === undefined ? 'a category' : named(r.category, 'Category');
  switch (r.signal) {
    case 'segment':
      return r.segment_buyers === undefined
        ? 'Segment: customers with the same label bought it'
        : `Segment: ${r.segment_buyers} other customers with the same label bought it in the window`;
    case 'preferred_category':
      return `Preferred category: the customer registered an interest in ${category}`;
    case 'purchase_history':
      return r.share === undefined
        ? `Purchase history: the customer buys from ${category}`
        : `Purchase history: ${category} is ${shareText(r.share)} of the customer's buying`;
    case 'relation_substitute':
      return 'Declared substitute for the unavailable product';
    default:
      return r.signal;
  }
}

export function availabilityText(a: AvailabilityView, kind: string): string {
  const where = named(a.store, 'Store');
  const channel = a.channel === undefined ? '' : ` on ${named(a.channel, 'Channel')}`;
  return kind === 'elsewhere'
    ? `${a.quantity_on_hand} on hand at ${where}${channel}, another store`
    : `${a.quantity_on_hand} on hand at ${where}${channel}`;
}

/**
 * The kind column's text: the label first, so it reads without colour, then what the kind adds. An
 * alternative names the product it replaces; an elsewhere entry says the product is unavailable at
 * the reference store and is not a recommendation for it.
 */
export function kindText(item: InspectedItemView, reference: NamedView | undefined): string {
  const why = item.unavailable_reason === undefined ? '' : ` (${unavailableText(item.unavailable_reason)})`;
  if (item.kind === 'alternative') {
    return item.alternative_for === undefined
      ? 'Alternative (the product it replaces was not named)'
      : `Alternative for ${named(item.alternative_for, 'Product')}, which is unavailable here${why}`;
  }
  if (item.kind === 'elsewhere') {
    const here = reference === undefined ? 'the reference store' : named(reference, 'Store');
    return `Another store: unavailable at ${here}${why}, so not recommended there`;
  }
  return kindLabel(item.kind);
}

/** RN-40's ordering, stated once so a reader knows what the factors are for. */
export const ORDER_NOTE =
  'Recommended products are ordered by signals matched, then segment buyers, then category share, then product id (RN-40). There are no weights: every position is explained by these figures.';

export const storeSourceText = (source: string | undefined): string =>
  source === 'given'
    ? 'named by the caller'
    : source === 'usual_store'
      ? "the customer's usual store"
      : (source ?? '');

/** The start of a day (UTC) as the instant the service reads. */
export function dayStart(date: string): string {
  return `${date}T00:00:00Z`;
}

/** The start of the day after a date (UTC): the service excludes `to`, so the chosen day is included. */
export function dayAfter(date: string): string {
  const t = Date.parse(`${date}T00:00:00Z`);
  return Number.isNaN(t) ? date : new Date(t + 86_400_000).toISOString().replace('.000Z', 'Z');
}
