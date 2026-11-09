// Plain words for what campaigns-service refuses, and for what a preview leaves out (F28-09, #878).
// Pure data and rules, so the main process and the renderer apply the same ones and a unit test
// can check them against the contract. A code and a correlation id are never replaced: only the
// sentence a person reads is.

import type { AudienceReasonCode, AudiencePreviewView, ErrorView, FieldDetail } from './api';

/**
 * One sentence for each code a /v1/campaigns operation can answer with, in contracts/campaigns/
 * openapi.yaml. The codes of the session (invalid_token, session_revoked) and of an unavailable
 * dependency (auth_unavailable, database_unavailable) are worded by the shell, not here.
 */
export const PLAIN_MESSAGES: Readonly<Record<string, string>> = {
  forbidden_role: 'Your role is not allowed to do this. Ask an administrator if you need access.',
  campaign_not_found: 'This campaign does not exist any more. Reload the list of campaigns.',
  campaign_not_draft:
    'Only a DRAFT campaign can be changed. Once a campaign is active, finished or cancelled, its dates, audience, budget, conditions and exclusions are locked.',
  duplicate_draft: 'A DRAFT campaign with the same name, label and dates already exists. Change one of them.',
  ends_before_start: 'The end date cannot be before the start date.',
  unknown_label: 'That segment label does not exist. Choose one from the list.',
  unknown_customer:
    'One or more customer ids are not in the customer list. Check the ids and remove the unknown ones.',
  invalid_body: 'Some values were not accepted. Correct the marked fields and save again.',
  malformed_body: 'The request could not be read. Try again, and report it if it keeps happening.',
  payload_too_large: 'Too much was sent at once. Send fewer customer ids.',
  unsupported_media_type:
    'The service did not accept the format of the request. This is a defect: report it.',
  not_acceptable:
    'The service could not answer in the format this app asks for. This is a defect: report it.',
  rate_limited: 'Too many requests were sent. Wait a moment and try again.',
  invalid_pagination: 'The page or the page size is outside what the service allows.',
  illegal_transition:
    'The campaign cannot make that move from its current status. A DRAFT can become ACTIVE or CANCELLED, an ACTIVE can become FINISHED or CANCELLED, and FINISHED and CANCELLED are final.',
  status_changed:
    'The status of this campaign changed after you opened it. It has been read again: check it and try again.',
  campaign_ended:
    'The end date of this campaign has passed, so it cannot be activated. Change the end date or cancel it.',
  experiment_not_ready:
    'An experiment attached to this campaign is not ready. It needs a treatment group, and a control group unless it has exactly two treatment groups. Fix the experiment, then activate the campaign.',
};

/** The refusal with a plain sentence for its code; a code with none keeps the service's own message. */
export function plainRefusal(error: ErrorView): ErrorView {
  const message = PLAIN_MESSAGES[error.code];
  return message === undefined ? error : { ...error, message };
}

const FIELD_NAMES: Readonly<Record<string, string>> = {
  name: 'The name',
  label_code: 'The label',
  starts_on: 'The start date',
  ends_on: 'The end date',
  max_customers: 'The maximum number of customers',
  total_amount: 'The total budget',
  per_customer_cap: 'The per-customer limit',
  registered_on_or_before: 'The registration date',
  min_purchases: 'The minimum number of purchases',
  purchase_window_days: 'The purchase window',
  customer_ids: 'The customer ids',
  label_codes: 'The excluded labels',
};

const DETAIL_PHRASES: Readonly<Record<string, string>> = {
  required: 'is required',
  too_long: 'is too long',
  out_of_range: 'is outside the allowed range',
  invalid_format: 'is not in the expected format',
  additional_property: 'cannot be set here',
  max_items: 'has more entries than are allowed',
  duplicate: 'lists the same value twice',
  required_together: 'must be given together with the other purchase rule, or both left empty',
  exceeds_total_amount: 'cannot be larger than the total budget',
  before_start: 'cannot be before the start date',
  unknown: 'is not known',
  unknown_customer: 'names a customer that is not in the customer list',
};

/** One field-level reason in words: "The per-customer limit cannot be larger than the total budget." */
export function plainDetail(detail: FieldDetail): string {
  const field = FIELD_NAMES[detail.field] ?? detail.field;
  const phrase = DETAIL_PHRASES[detail.code] ?? `was refused (${detail.code})`;
  return `${field} ${phrase}.`;
}

/**
 * Customers that hold the label and are not in the audience. With the breakdown it is the sum of the
 * reasons' counts; without it, the difference between the label's holders and the audience, which is
 * absent when the holders are unknown.
 */
export function leftOut(preview: AudiencePreviewView): number | undefined {
  if (preview.breakdown !== undefined)
    return preview.breakdown.reasons.reduce((sum, r) => sum + r.excluded, 0);
  if (preview.label_holders === undefined) return undefined;
  return Math.max(0, preview.label_holders - preview.audience.audience_size);
}

/**
 * One reason of the breakdown in words, with the rule it applies when that rule is set. A reason
 * whose rule is not set still has a row, with its count of 0, and says so.
 */
export function reasonText(
  code: AudienceReasonCode,
  preview: AudiencePreviewView,
  labelName: (code: string) => string,
): string {
  const { exclusions, eligibility, audience } = preview;
  const plural = (n: number, one: string, many: string): string => (n === 1 ? one : many);
  switch (code) {
    case 'not_in_customer_reference':
      return 'Not in the customer list';
    case 'excluded_customer':
      return exclusions.customer_ids.length === 0
        ? 'Excluded by id (none listed)'
        : `Excluded by id (${exclusions.customer_ids.length} listed)`;
    case 'excluded_label':
      return exclusions.label_codes.length === 0
        ? 'Excluded by label (none listed)'
        : `Excluded by label (${exclusions.label_codes.map(labelName).join(', ')})`;
    case 'registered_after_cutoff':
      return eligibility.registered_on_or_before === undefined
        ? 'Registered after the cutoff (no cutoff set)'
        : `Registered after ${eligibility.registered_on_or_before}`;
    case 'too_few_purchases':
      return eligibility.min_purchases === undefined || eligibility.purchase_window_days === undefined
        ? 'Too few purchases (no purchase condition set)'
        : `Fewer than ${eligibility.min_purchases} net ${plural(eligibility.min_purchases, 'purchase', 'purchases')} in the ${eligibility.purchase_window_days} days before the preview`;
    case 'over_max_customers':
      return audience.max_customers === undefined
        ? 'Above the cap (no cap set)'
        : `Above the cap of ${audience.max_customers} customers`;
  }
}

export interface Reason {
  id: 'customers' | 'labels' | 'registered' | 'purchases' | 'cap' | 'none';
  text: string;
}

/**
 * Why customers can be left out, from the rules stored. Used only when the breakdown could not be
 * read: the audience's one total is then all the service said, so each reason is a rule in force, not
 * a count of its own.
 */
export function reasonsOf(preview: AudiencePreviewView, labelName: (code: string) => string): Reason[] {
  const out: Reason[] = [];
  const { exclusions, eligibility, audience } = preview;
  if (exclusions.customer_ids.length > 0)
    out.push({
      id: 'customers',
      text: `Excluded by id: ${exclusions.customer_ids.length} ${exclusions.customer_ids.length === 1 ? 'customer is' : 'customers are'} listed and left out.`,
    });
  if (exclusions.label_codes.length > 0)
    out.push({
      id: 'labels',
      text: `Excluded by label: holders of ${exclusions.label_codes.map(labelName).join(', ')} are left out.`,
    });
  if (eligibility.registered_on_or_before !== undefined)
    out.push({
      id: 'registered',
      text: `Condition: only customers registered on or before ${eligibility.registered_on_or_before}.`,
    });
  if (eligibility.min_purchases !== undefined && eligibility.purchase_window_days !== undefined)
    out.push({
      id: 'purchases',
      text: `Condition: only customers with at least ${eligibility.min_purchases} net ${eligibility.min_purchases === 1 ? 'purchase' : 'purchases'} in the ${eligibility.purchase_window_days} days before the preview. A sale whose net amount is zero does not count.`,
    });
  if (audience.max_customers !== undefined)
    out.push({
      id: 'cap',
      text: `Cap: the audience is cut to at most ${audience.max_customers} customers.`,
    });
  if (out.length === 0)
    out.push({ id: 'none', text: 'No exclusion, condition or cap is set, so no rule leaves anyone out.' });
  return out;
}
