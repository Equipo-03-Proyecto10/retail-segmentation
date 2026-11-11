// Types shared by the main process, the preload bridge and the screens.
// Nothing in here carries a credential.

import type {
  ConsentView,
  ExplorationProfileView,
  ExplorationQuery,
  ExplorationSalesView,
  InterestsView,
  RestrictionsView,
} from './exploration';

export interface FieldDetail {
  field: string;
  code: string;
}

/** The common/v1 Error envelope as shown on screen. */
export interface ErrorView {
  code: string;
  message: string;
  correlation_id: string;
  details: FieldDetail[];
  /** The Retry-After header of a 429, as sent. */
  retry_after?: string;
}

export interface ServiceView {
  name: string;
  resources: string[];
}

/** os_keyring: tokens sealed by the OS keyring and kept across restarts. memory_only: no keyring. */
export type Storage = 'os_keyring' | 'memory_only';

export type AppState =
  | { status: 'signed_out'; storage: Storage; notice?: string }
  | {
      status: 'signed_in';
      storage: Storage;
      role: string;
      services: ServiceView[];
      /** operationIds this role may call, from the contracts' x-mosaiq-roles. Screens show a write control only when its operation is here. */
      operations: string[];
      /** Whether this role may write a table to a file (ADR-0074). */
      canExport: boolean;
    };

/** A table the user is looking at, sent to the main process to be written through a save dialog. */
export interface ExportRequest {
  module: string;
  columns: string[];
  rows: string[][];
}

/** null: the user closed the save dialog and nothing was written. */
export type ExportOutcome = { rows: number; file: string } | null;

export type ActionResult = { ok: true; state: AppState } | { ok: false; error: ErrorView; state: AppState };

export type ProbeResult =
  | { ok: true; service: string; status: number; root: string; correlation_id: string }
  | { ok: false; error: ErrorView };

/** A named operation's answer: its value, or the error envelope as returned. */
export type Result<T> = { ok: true; value: T } | { ok: false; error: ErrorView };

export interface PageView {
  page: number;
  size: number;
  total: number;
}

export interface Paged<T> {
  items: T[];
  page: PageView;
}

export interface ProductView {
  product_id: number;
  sku: string;
  name: string;
  category_id: number;
  /** Read from categories-service, never from products-service. */
  category_name: string;
  list_price: string;
  is_active: boolean;
  image_path?: string;
}

export interface ProductQuery {
  q?: string;
  sku?: string;
  category_id?: number;
  active?: boolean;
  page?: number;
  size?: number;
}

export interface ProductDraft {
  product_id: number;
  sku: string;
  name: string;
  category_id: number;
  list_price: string;
}

export interface ProductChanges {
  name?: string;
  category_id?: number;
  list_price?: string;
  is_active?: boolean;
}

export interface CategoryView {
  category_id: number;
  name: string;
  parent_category_id: number | null;
}

export interface CategoryNode extends CategoryView {
  children: CategoryNode[];
}

export interface CategoryDraft {
  category_id: number;
  name: string;
  parent_category_id: number | null;
}

/** parent_category_id null makes the category a root. */
export interface CategoryChanges {
  name?: string;
  parent_category_id?: number | null;
}

export interface StoreView {
  store_id: number;
  name: string;
  city: string;
  state: string;
  is_active: boolean;
}

export interface StoreLocationView {
  /** Decimal degrees, as stores-service returned them. */
  latitude: string;
  longitude: string;
  geofence_radius_m: number;
  set_at: string;
}

/** A store of the directory, with its location when one is set. */
export interface StoreDetailView extends StoreView {
  location: StoreLocationView | null;
}

export interface StoreQuery {
  active?: boolean;
  city?: string;
  page?: number;
  size?: number;
}

/** Typed values, sent as written: the main process checks the bounds before anything is sent. */
export interface StoreLocationDraft {
  latitude: string;
  longitude: string;
  geofence_radius_m: string;
}

export type CampaignStatus = 'DRAFT' | 'ACTIVE' | 'FINISHED' | 'CANCELLED';

export interface CampaignView {
  campaign_id: number;
  name: string;
  label_code: string;
  /** Calendar dates, YYYY-MM-DD, as campaigns-service returned them. */
  starts_on: string;
  ends_on: string;
  status: CampaignStatus;
  /** The moves campaigns-service allows from the status shown. */
  allowed_transitions: CampaignStatus[];
}

export interface CampaignQuery {
  status?: CampaignStatus;
  label_code?: string;
  page?: number;
  size?: number;
}

/** The audited tables, as fn_audit records them (audit-trail.xsd AuditedEntity). */
export const AUDITED_ENTITIES = [
  'category',
  'product',
  'store',
  'customer',
  'app_user',
  'segment',
  'segment_rule',
  'campaign',
  'experiment',
  'channel',
  'role',
  'customer_segment_history',
  'store_location',
] as const;
export const AUDIT_ACTIONS = ['INSERT', 'UPDATE', 'DELETE'] as const;

export interface AuditQuery {
  entity?: string;
  /** Only valid together with entity. */
  entity_pk?: string;
  user_id?: string;
  action?: string;
  /** RFC 3339 timestamps with an offset. */
  from?: string;
  to?: string;
  page?: number;
  size?: number;
}

export interface CampaignDraft {
  name: string;
  label_code: string;
  starts_on: string;
  ends_on: string;
}

/** Only the fields that changed. */
export type CampaignChanges = Partial<CampaignDraft>;

/** A segment label, from segments-service: the vocabulary a campaign targets. */
export interface LabelView {
  label_code: string;
  name: string;
}

/**
 * A campaign's plan: four sub-resources of its own path (F25-03, #834), each read on its own and
 * replaced whole while the campaign is DRAFT. Optional members are absent when never written.
 */
export interface AudienceView {
  campaign_id: number;
  label_code: string;
  /** The cap on the number of customers; absent when there is none. */
  max_customers?: number;
  /** Counted by campaigns-service when it answered, as of as_of; never stored by the client. */
  audience_size: number;
  as_of: string;
}

/** Typed values, sent as written: the main process checks them. Empty text removes the cap. */
export interface AudienceDraft {
  max_customers: string;
}

export interface BudgetView {
  campaign_id: number;
  /** Two-decimal text, no currency: the most the campaign may grant in discounts. */
  total_amount?: string;
  /** Two-decimal text, no currency: the most one customer may receive. */
  per_customer_cap?: string;
}

/** Two-decimal amounts as typed; empty text removes that limit. */
export interface BudgetDraft {
  total_amount: string;
  per_customer_cap: string;
}

/** The conditions (eligibility rules) a customer in the audience must satisfy. */
export interface EligibilityView {
  campaign_id: number;
  registered_on_or_before?: string;
  min_purchases?: number;
  purchase_window_days?: number;
}

/** As typed; empty text removes a rule, and the two purchase members go together. */
export interface EligibilityDraft {
  registered_on_or_before: string;
  min_purchases: string;
  purchase_window_days: string;
}

export interface ExclusionsView {
  campaign_id: number;
  customer_ids: string[];
  label_codes: string[];
}

export interface ExclusionsDraft {
  customer_ids: string[];
  label_codes: string[];
}

/** Why a customer holding the campaign's label is not in its audience, in the order of precedence the contract fixes. */
export const AUDIENCE_REASONS = [
  'not_in_customer_reference',
  'personalization_withdrawn',
  'marketing_contact_withdrawn',
  'excluded_customer',
  'excluded_label',
  'registered_after_cutoff',
  'too_few_purchases',
  'over_max_customers',
] as const;
export type AudienceReasonCode = (typeof AUDIENCE_REASONS)[number];

export interface AudienceReasonView {
  code: AudienceReasonCode;
  /** Customers left out for this reason and no earlier one. */
  excluded: number;
}

/**
 * The audience with the customers it leaves out counted by reason (getCampaignAudienceBreakdown).
 * Each customer is counted once, under the first reason that applies, so label_holders is the sum of
 * the reasons' counts plus audience_size.
 */
export interface AudienceBreakdownView {
  campaign_id: number;
  label_code: string;
  /** Customers holding the label before any reason is applied. */
  label_holders: number;
  /** Always eight, in the contract's order. */
  reasons: AudienceReasonView[];
  audience_size: number;
  as_of: string;
}

/**
 * What a preview shows: the audience with its exclusions counted by reason, and the rules it was
 * counted under (the cap, the conditions and the exclusions, to word each reason).
 *
 * When campaigns-service does not answer the breakdown, `breakdown` is absent, `breakdown_error` says
 * why, and the preview falls back to the audience's one total: the number left out is then the
 * customers holding the label now (segments-service, `label_holders`) minus the audience, and it is
 * not split by reason.
 */
export interface AudiencePreviewView {
  audience: AudienceView;
  eligibility: EligibilityView;
  exclusions: ExclusionsView;
  breakdown?: AudienceBreakdownView;
  breakdown_error?: ErrorView;
  /** Fallback only: customers holding the label now, from segments-service; absent when that read failed. */
  label_holders?: number;
}

/** One append-only entry. audit_id is text: it can exceed 2^53. The payloads are JSON text, as returned. */
export interface AuditEntryView {
  audit_id: string;
  user_id: string | null;
  entity: string;
  entity_pk: string;
  action: string;
  data_before: string | null;
  data_after: string | null;
  executed_at: string;
}

export interface StockQuery {
  store_id?: number;
  below?: number;
  page?: number;
  size?: number;
}

/** A stock level carries ids only; the names are read from products-service and stores-service. */
export interface StockLevelView {
  store_id: number;
  store_name: string;
  product_id: number;
  product_name: string;
  quantity_on_hand: number;
  updated_at: string;
}

export interface SalesQuery {
  customer_id?: string;
  store_id?: number;
  /** RFC 3339 date-times. */
  from?: string;
  to?: string;
  page?: number;
  size?: number;
}

export interface SaleLineView {
  product_id: number;
  product_name: string;
  quantity: number;
  unit_price: string;
  /** quantity × unit_price, worked out in cents. */
  line_total: string;
}

export interface SaleView {
  sale_id: number;
  customer_id: string;
  store_id: number;
  store_name: string;
  channel_id: number;
  channel_name: string;
  occurred_at: string;
  /** As returned by sales-service. */
  total: string;
  lines: SaleLineView[];
}

export type GroupBy = 'store' | 'day' | 'type';

export interface SummaryQuery {
  /** YYYY-MM-DD. */
  from?: string;
  to?: string;
  store_id?: number;
  group_by: GroupBy;
}

export interface SummaryView {
  from?: string;
  to?: string;
  group_by: GroupBy;
  /** label is the store name when grouped by store, otherwise the key. */
  groups: { key: string; label: string; count: number }[];
}

export interface EventsQuery {
  type?: 'QR_SCAN' | 'RECO_CLICK';
  store_id?: number;
  customer_id?: string;
  voided?: boolean;
  from?: string;
  to?: string;
  page?: number;
  size?: number;
}

export interface HistoryEntryView {
  action: string;
  reason: string;
  by: string;
  at: string;
}

export interface EngagementEventView {
  event_id: string;
  type: string;
  store_id: number;
  store_name: string;
  product_id: number;
  product_name: string;
  customer_id?: string;
  label_code?: string;
  rank?: number;
  recorded_by: string;
  occurred_at: string;
  recorded_at: string;
  voided: boolean;
  history: HistoryEntryView[];
}

export interface CheckInsQuery {
  store_id?: number;
  user_id?: string;
  verified?: boolean;
  from?: string;
  to?: string;
  page?: number;
  size?: number;
}

export interface CheckInView {
  check_in_id: string;
  user_id: string;
  store_id: number;
  store_name: string;
  latitude?: string;
  longitude?: string;
  accuracy_m?: string;
  distance_m?: string;
  reason: string;
  auto_verified: boolean;
  verified: boolean;
  occurred_at: string;
  recorded_at: string;
}

export interface ReviewView {
  review_id: number;
  verified: boolean;
  reason: string;
  reviewed_by: string;
  reviewed_at: string;
}

export interface CheckInDetailView extends CheckInView {
  /** In the order the service returns them, oldest first. */
  reviews: ReviewView[];
}

export interface UserView {
  user_id: string;
  name: string;
  email: string;
  role_code: string;
  is_active: boolean;
  created_at: string;
}

export interface RoleView {
  role_code: string;
  description: string;
}

export interface UserQuery {
  role?: string;
  active?: boolean;
  q?: string;
  page?: number;
  size?: number;
}

/** The password travels to users-service and is never returned or shown back. */
export interface UserDraft {
  name: string;
  email: string;
  role_code: string;
  password: string;
}

export interface UserChanges {
  name?: string;
  email?: string;
  role_code?: string;
}

export interface CustomerView {
  customer_id: string;
  name: string;
  email?: string;
  phone?: string;
  registration_channel_id: number;
  /** Read from sales-service, never from customers-service. */
  registration_channel_name: string;
  registered_on: string;
}

export interface CustomerQuery {
  q?: string;
  email?: string;
  page?: number;
  size?: number;
}

/** Only the fields that change are sent; an absent field is left as it is. */
export interface CustomerChanges {
  name?: string;
  email?: string;
  phone?: string;
  registration_channel_id?: number;
}

export interface ChoiceView {
  id: number;
  name: string;
}

/** The profile as customer-preferences returned it, with names read from sales-service and categories-service. */
export interface PreferencesView {
  customer_id: string;
  channels: ChoiceView[];
  categories: ChoiceView[];
  /** Every channel and category that can be chosen; empty when its service could not be read. */
  channel_options: ChoiceView[];
  category_options: ChoiceView[];
}

export type AssignmentState = 'assigned' | 'unassigned_result' | 'no_open_assignment';

export interface SegmentLabelView {
  label_code: string;
  name: string;
  description?: string;
  ordinal_position: number;
  size: number;
}

/** The label vocabulary, sorted by ordinal_position (the business order). */
export interface SegmentVocabularyView {
  run_id?: number;
  run_at?: string;
  unassigned_count: number;
  labels: SegmentLabelView[];
}

/** Exactly what segments-service returned; a field it left out stays absent. */
export interface CurrentAssignmentView {
  customer_id: string;
  state: string;
  label_code?: string;
  /** From the label vocabulary; absent when the state carries no label. */
  label_name?: string;
  r_score?: number;
  f_score?: number;
  m_score?: number;
  since?: string;
  run_id?: number;
}

export interface AssignmentRowView {
  customer_id: string;
  run_id: number;
  label_code?: string;
  label_name?: string;
  r_score?: number;
  f_score?: number;
  m_score?: number;
  valid_from: string;
  valid_to?: string;
}

export interface AssignmentQuery {
  customer_id?: string;
  label_code?: string;
  /** Only open rows (valid_to empty). */
  open?: boolean;
  page?: number;
  size?: number;
}

export interface ReasonView {
  signal: string;
  segment_buyers?: number;
  category_id?: number;
  category_name?: string;
  share?: string;
}

export interface RecommendationItemView {
  product_id: number;
  /** Read from products-service with one call per list. */
  product_name: string;
  category_id: number;
  category_name: string;
  quantity_on_hand: number;
  reasons: ReasonView[];
}

export interface RecommendationView {
  customer_id: string;
  /** recommended, none_match, no_open_assignment, unassigned_result or no_usual_store, as returned. */
  status: string;
  computed_at: string;
  window: { days: number; start: string; end: string };
  label_code?: string;
  run_id?: number;
  usual_store_id?: number;
  items: RecommendationItemView[];
}

// ---------- executive reports (F28-14, #883) ----------

export interface MeasureDefinitionView {
  code: string;
  kind: string;
  unit: string;
  definition: string;
}

/** One KPI of the analytics-kpis catalog: what a figure means, over which window, by which dimensions. */
export interface KpiDefinitionView {
  kpi_code: string;
  name: string;
  version: number;
  definition: string;
  window: string;
  dimensions: string[];
  measures: MeasureDefinitionView[];
}

export interface ReportQuery {
  kpi: string;
  from: string;
  to: string;
  group_by?: string[];
  granularity?: string;
  store_id?: number;
  segment_code?: string;
  channel_id?: number;
}

export interface ReportMeasureView {
  code: string;
  kind: string;
  unit: string;
  /** The decimal exactly as the service sent it; null when the figure cannot be computed. */
  value: string | null;
  lower?: string;
  upper?: string;
}

export interface ReportRowView {
  period_start?: string;
  period_end?: string;
  segment_code?: string;
  segment_state?: string;
  from_segment_code?: string;
  store?: string;
  channel?: string;
  campaign_id?: string;
  model_version_id?: string;
  synthetic: boolean;
  measures: ReportMeasureView[];
}

/** A report as served, with its KPI's definition. Exports are written from this and nothing else. */
export interface ReportView {
  kpi: string;
  name: string;
  definition_version: number;
  from: string;
  to: string;
  granularity?: string;
  group_by: string[];
  generated_at: string;
  store_restricted: boolean;
  /** Some figure comes from seeded data (ADR-0052). */
  synthetic: boolean;
  spans_model_versions: boolean;
  definition: KpiDefinitionView;
  rows: ReportRowView[];
  /** Rows the service holds for this query; larger than rows.length when the report was cut. */
  rows_total: number;
}

export type ReportFormat = 'pdf' | 'csv' | 'xlsx';

// ---------- recommendations inspection (F28-08, #877) ----------

/** An id the service sent, with the name its owner gave it; absent when that owner could not be read. */
export interface NamedView {
  id: number;
  name?: string;
}

export interface FactorsView {
  signals_matched: number;
  segment_buyers?: number;
  /** The decimal as recommendations-service sent it. */
  category_share?: string;
}

export interface AvailabilityView {
  /** The store whose shelf the figure comes from: the reference store, except on an elsewhere entry. */
  store: NamedView;
  channel?: NamedView;
  quantity_on_hand: number;
}

export interface InspectedReasonView {
  signal: string;
  segment_buyers?: number;
  category?: NamedView;
  share?: string;
}

export interface InspectedItemView {
  product: NamedView;
  category: NamedView;
  /** primary: available here and recommended. alternative: a declared substitute for an unavailable product. elsewhere: the unavailable product, held by another store. */
  kind: string;
  position: number;
  /** Absent on an alternative, which is not scored. */
  factors?: FactorsView;
  availability: AvailabilityView;
  /** The unavailable product an alternative stands in for. */
  alternative_for?: NamedView;
  unavailable_reason?: string;
  reasons: InspectedReasonView[];
}

export interface ExclusionView {
  reason: string;
  count: number;
}

/** What recommendations-service answered for a customer, or what it recorded in a decision: the same shape, since a decision holds what the client received. */
export interface InspectionView {
  decision_id: string;
  /** Absent once the customer was anonymized. */
  customer_id?: string;
  status: string;
  computed_at: string;
  /** Only on a live answer; a stored decision does not keep the window. */
  window?: { days: number; start: string; end: string };
  label_code?: string;
  run_id?: number;
  model_version_id?: number;
  /** Only on a live answer. */
  usual_store?: NamedView;
  /** The reference store the products were judged at. */
  store?: NamedView;
  /** given: the caller named it. usual_store: the customer's usual store. */
  store_source?: string;
  channel?: NamedView;
  exclusions: ExclusionView[];
  items: InspectedItemView[];
}

export interface InspectQuery {
  customer_id: string;
  store_id?: number;
  channel_id?: number;
  limit?: number;
}

export interface DecisionQuery {
  customer_id?: string;
  store_id?: number;
  status?: string;
  /** RFC 3339 instants: from is included, to is not. */
  from?: string;
  to?: string;
  page?: number;
  size?: number;
}

export interface DecisionSummaryView {
  decision_id: string;
  customer_id?: string;
  status: string;
  computed_at: string;
  store?: NamedView;
  store_source?: string;
  channel?: NamedView;
  item_count: number;
  exclusions: ExclusionView[];
}

// ---------- drift analysis (F28-07, #876) ----------

/** DATA_DRIFT compares distributions with the version's reference; OUTCOME_TEST tests a matured cohort (ADR-0065). */
export type DriftKind = 'DATA_DRIFT' | 'OUTCOME_TEST';

/** Whether a number exists: INSUFFICIENT_DATA and NOT_COMPARED carry no statistic (ADR-0064, ADR-0065). */
export type DriftComparison = 'COMPARED' | 'INSUFFICIENT_DATA' | 'NOT_COMPARED';

export type DistributionVariable = 'SEGMENT_SHARES' | 'R' | 'F' | 'M';
export type DriftVariable = DistributionVariable | 'REPURCHASE_RATE';
export type VariableState = 'NORMAL' | 'WATCH' | 'ALARM_PENDING' | 'ALARM';
export type StatisticLevel = 'NORMAL' | 'WATCH' | 'ALARM';
export type OutcomeResult = 'PASSED' | 'NOT_PASSED' | 'NOT_TESTED';

/** The only wording a screen may use for a finding. CONCEPT_DRIFT belongs to an outcome-test alert alone. */
export type DriftClaim = 'DISTRIBUTION_CHANGE' | 'CONCEPT_DRIFT';

export type ReviewCode = 'REVIEW_SEGMENT_MIX' | 'REVIEW_FEATURE_DISTRIBUTION' | 'REVIEW_PREDICTIVE_RELATION';

export interface ModelVersionRefView {
  version_id: number;
  name: string;
  method: string;
}

/** DATA_DRIFT: the trailing days ending at end. OUTCOME_TEST: the outcome window (start, end] of one cohort. */
export interface DriftWindowView {
  start: string;
  end: string;
  days: number;
  customer_count: number;
}

export interface DriftReferenceView {
  sample_size: number;
  captured_at: string;
}

/** Decimals are kept exactly as the service sent them. lower and upper are absent for a segment and for NO_VALUE. */
export interface DriftBinView {
  label: string;
  lower?: string;
  upper?: string;
  reference_share: string;
  current_share: string;
  contribution: string;
}

export interface DriftStatisticView {
  statistic: 'PSI' | 'CHI_SQUARE';
  value: string;
  /** CHI_SQUARE only. */
  p_value?: string;
  degrees_of_freedom?: number;
  /** VALUE: reached at or above a threshold (PSI). P_VALUE: below it (CHI_SQUARE). */
  threshold_basis: 'VALUE' | 'P_VALUE';
  watch_threshold: string;
  alarm_threshold: string;
  level: StatisticLevel;
}

export interface VariableDriftView {
  variable: DistributionVariable;
  state: VariableState;
  statistics: DriftStatisticView[];
  bins: DriftBinView[];
}

export interface OutcomeRatesView {
  customers: number;
  repurchasers: number;
  /** Absent when there are no customers. */
  rate?: string;
}

export interface OutcomeSegmentView {
  label_code: string;
  reference: OutcomeRatesView;
  current: OutcomeRatesView;
  difference_points?: string;
  /** z, p_value and adjusted_p_value are absent when result is NOT_TESTED. */
  z?: string;
  p_value?: string;
  adjusted_p_value?: string;
  result: OutcomeResult;
  persisted: boolean;
}

export interface OutcomeTestView {
  relation: 'REPURCHASE_GIVEN_SEGMENT';
  horizon_days: number;
  cohort_as_of: string;
  thresholds: { alpha: string; min_difference_points: string; min_customers_per_side: number };
  segments: OutcomeSegmentView[];
  /** Present only when a segment persisted; the one place the phrase concept drift may come from. */
  claim?: 'CONCEPT_DRIFT';
}

export interface SuggestedReviewView {
  code: ReviewCode;
  /** English text from the service, shown as it is. */
  text: string;
}

export interface DriftReportView {
  report_id: number;
  kind: DriftKind;
  model_version: ModelVersionRefView;
  comparison: DriftComparison;
  not_compared_reason?: 'VERSION_CHANGE';
  window: DriftWindowView;
  reference: DriftReferenceView;
  previous_report_id?: number;
  /** Empty unless the report is a COMPARED DATA_DRIFT. */
  variables: VariableDriftView[];
  /** Present only on a COMPARED OUTCOME_TEST. */
  outcome_test?: OutcomeTestView;
  alert_ids: number[];
  computed_at: string;
}

export interface DriftAlertView {
  alert_id: number;
  report_id: number;
  previous_report_id: number;
  kind: DriftKind;
  claim: DriftClaim;
  model_version: ModelVersionRefView;
  variable: DriftVariable;
  window: DriftWindowView;
  reference: DriftReferenceView;
  /** A DATA_DRIFT alert: its statistics against their thresholds. Empty for an outcome test. */
  statistics: DriftStatisticView[];
  /** A DATA_DRIFT alert: the (at most three) bins that moved most, largest contribution first. */
  bins: DriftBinView[];
  /** An OUTCOME_TEST alert: the segments that persisted. */
  outcome_test?: OutcomeTestView;
  suggested_review: SuggestedReviewView;
  raised_at: string;
}

export interface DriftReportQuery {
  kind?: DriftKind;
  model_version_id?: number;
  comparison?: DriftComparison;
  page?: number;
  size?: number;
}

export interface DriftAlertQuery {
  kind?: DriftKind;
  model_version_id?: number;
  variable?: DriftVariable;
  page?: number;
  size?: number;
}

// ---------- migration matrix (F28-06, #875) ----------

/** How a customer stood at one instant: holding a label, scored with none (RN-21), or with no assignment (new). */
export type MigrationState = 'label' | 'unassigned' | 'new';

/** Set by segments-service from the vocabulary's order; the client never recomputes it. */
export type MigrationMovement = 'improved' | 'declined' | 'unchanged' | 'not_ranked';

/** The two instants of a comparison. The earlier one is always the before state (RN-59). */
export interface MigrationWindow {
  from: string;
  to: string;
}

/** One state's figures. label_code is present exactly when state is label. */
export interface MigrationTotalView {
  state: MigrationState;
  label_code?: string;
  before_count: number;
  after_count: number;
  entries: number;
  exits: number;
}

/** A matrix cell: the customers who stood in one state before and one state after. A cell with none is not sent. */
export interface MigrationCellView {
  previous_state: MigrationState;
  previous_label_code?: string;
  new_state: MigrationState;
  new_label_code?: string;
  movement: MigrationMovement;
  customer_count: number;
}

export interface MigrationMatrixView {
  from: string;
  to: string;
  /** Absent while the latest versioned run at that instant has none yet. */
  from_model_version_id?: number;
  to_model_version_id?: number;
  /** The two instants fall under different model versions, or one is unknown: a difference may come from the model (ADR-0064). */
  methodological: boolean;
  customer_count: number;
  totals: MigrationTotalView[];
  cells: MigrationCellView[];
}

export type MigrationDirection = 'entries' | 'exits';

/**
 * The customers behind a cell (both states, and their label codes when the state is label) or behind one
 * label's entries or exits (direction and label_code); never both.
 */
export interface MigrationListQuery extends MigrationWindow {
  previous_state?: MigrationState;
  previous_label_code?: string;
  new_state?: MigrationState;
  new_label_code?: string;
  direction?: MigrationDirection;
  label_code?: string;
  /** false keeps behaviour only; true keeps what a model change may explain. */
  methodological?: boolean;
  page?: number;
  size?: number;
}

export interface MigrationView {
  customer_id: string;
  previous_state: MigrationState;
  previous_label_code?: string;
  new_state: MigrationState;
  new_label_code?: string;
  movement: MigrationMovement;
  changed_at?: string;
  cause?: string;
  previous_run_id?: number;
  new_run_id?: number;
  previous_model_version_id?: number;
  new_model_version_id?: number;
  methodological: boolean;
  /** recency, frequency or monetary: the measures judged changed. */
  responsible_variables: string[];
  /** The label changed and no variable did: the score moved because the population or the model did. */
  rank_only: boolean;
}

export interface MigrationListView extends MigrationWindow {
  items: MigrationView[];
  page: PageView;
}

export interface MigrationExplanationQuery extends MigrationWindow {
  customer_id: string;
}

export interface MigrationMeasureView {
  variable: string;
  unit: string;
  /** Decimals exactly as the service sent them; absent when the side has no value. */
  previous_value?: string;
  new_value?: string;
  judgement: 'changed' | 'stable' | 'not_compared';
  not_compared_reason?: string;
  rank_changed: boolean;
  statement: string;
}

export interface MigrationExplanationView extends MigrationWindow {
  migration: MigrationView;
  measures: MigrationMeasureView[];
}

// ---------- A/B experiments (F28-10, #879) ----------

export type ExperimentState = 'PLANNED' | 'ASSIGNED' | 'ANALYZED';

/** CONTROLLED is the only design that may state an effect; OFFER_COMPARISON has no control (ADR-0068). */
export type ExperimentDesignKind = 'CONTROLLED' | 'OFFER_COMPARISON';

export type ArmKind = 'CONTROL' | 'TREATMENT';

export type ExperimentMetric = 'CONVERSION' | 'REVENUE_PER_CUSTOMER';

/** SEEDED and INJECTED figures are Synthetic (RN-26). */
export type ExperimentDataOrigin = 'OBSERVED' | 'SEEDED' | 'INJECTED';

export type ExperimentFlag =
  'UNDERPOWERED' | 'SAMPLE_RATIO_FAILED' | 'BALANCE_FLAGGED' | 'RECOMPUTATION_MISMATCH';

export interface ExperimentArmView {
  arm_id: number;
  position: number;
  kind: ArmKind;
  name: string;
  treatment_description: string;
}

/**
 * What was fixed before anyone was assigned (ADR-0068). Decimals stay as the service sent them. Every
 * value but the metric, the window and the start is absent for assignment rule V0.
 */
export interface ExperimentDesignView {
  primary_metric: ExperimentMetric;
  secondary_metric?: ExperimentMetric;
  estimator?: 'DIFFERENCE_OF_PROPORTIONS' | 'DIFFERENCE_OF_MEANS';
  alpha?: string;
  comparison_alpha?: string;
  power?: string;
  minimum_detectable_effect?: string;
  baseline?: string;
  baseline_std_dev?: string;
  /** The customers each arm needs to detect the minimum effect; the service computes it at planning. */
  required_per_arm?: number;
  /** The smallest effect the frame can detect; above the minimum exactly when the plan is UNDERPOWERED. */
  smallest_detectable_effect?: string;
  allocation?: 'EQUAL';
  conversion_window_days: number;
  settle_days?: number;
  starts_on: string;
  /** Null until the experiment is assigned. */
  analysis_at?: string;
}

export interface FrameExclusionView {
  /** An open vocabulary: an unknown code is shown as it is. */
  reason: string;
  count: number;
}

export interface FrameView {
  taken_at: string;
  size: number;
  digest: string;
  label_code: string;
  model_version_id?: number;
  exclusions: FrameExclusionView[];
}

export interface SampleRatioView {
  status: 'PASSED' | 'FAILED';
  p_value: string;
  threshold: string;
}

export type BalanceMeasure = 'PRE_RECENCY_DAYS' | 'PRE_PURCHASE_COUNT' | 'PRE_NET_SPEND';

/** A diagnostic: it never changes an estimate and is never used to redraw an assignment. */
export interface BalanceView {
  measure: BalanceMeasure;
  arm_id: number;
  reference_arm_id: number;
  standardized_mean_difference: string;
  flagged: boolean;
  threshold: string;
}

export interface ExperimentChecksView {
  sample_ratio: SampleRatioView;
  balance: BalanceView[];
}

export interface AssignmentChecksView extends ExperimentChecksView {
  /** Recomputing every assignment from the plan gave the stored arms. */
  recomputation_matches: boolean;
}

export interface ArmCountView {
  arm_id: number;
  kind: ArmKind;
  assigned: number;
}

export interface AssignmentView {
  experiment_id: number;
  assigned_at: string;
  assigned_by?: string;
  assigned_total: number;
  acknowledged_underpowered: boolean;
  arms: ArmCountView[];
  /** Absent for assignment rule V0, which stored none. */
  checks?: AssignmentChecksView;
}

export interface ExperimentView {
  experiment_id: number;
  name: string;
  campaign_id?: number;
  state: ExperimentState;
  design_kind: ExperimentDesignKind;
  /** V1 is recomputable; V0 is the web's earlier shuffle: readable, never called verified. */
  assignment_rule: 'V0' | 'V1';
  data_origin: ExperimentDataOrigin;
  arms: ExperimentArmView[];
  design: ExperimentDesignView;
  frame?: FrameView;
  /** Published with the plan; it is not a secret. */
  salt?: string;
  assignment?: AssignmentView;
  flags: ExperimentFlag[];
  planned_at?: string;
  planned_by?: string;
}

export interface ExperimentQuery {
  campaign_id?: number;
  state?: ExperimentState;
  data_origin?: ExperimentDataOrigin;
  page?: number;
  size?: number;
}

export interface ExperimentArmDraft {
  kind: ArmKind;
  name: string;
  treatment_description: string;
}

/** The inputs of a plan. The baseline, the sample size, the frame and the salt are never inputs. */
export interface ExperimentDraft {
  name: string;
  campaign_id: number;
  primary_metric: ExperimentMetric;
  starts_on: string;
  conversion_window_days: number;
  /** A decimal as typed: a rate difference for CONVERSION (0.02 is two points), money for REVENUE_PER_CUSTOMER. */
  minimum_detectable_effect: string;
  alpha?: string;
  power?: string;
  arms: ExperimentArmDraft[];
}

// ---------- conversion analysis (F28-11, #880) ----------

/** PRIMARY is the result stored once at the analysis instant; RE_READ is computed at the request and replaces nothing. */
export type ResultBasis = 'PRIMARY' | 'RE_READ';

/** PENDING shows counts only; INVALID withholds the estimate because the sample ratio check failed. */
export type ResultStatus = 'PENDING' | 'COMPLETE' | 'INVALID';

/** EFFECT is a treatment against the control; TREATMENT_DIFFERENCE is one treatment against another and never an effect. */
export type ComparisonLabel = 'EFFECT' | 'TREATMENT_DIFFERENCE';

export type MetricRole = 'PRIMARY' | 'SECONDARY';

/** INCONCLUSIVE is a non-significant result of an underpowered experiment: never "no effect" (ADR-0068). */
export type Conclusion = 'SIGNIFICANT' | 'NOT_SIGNIFICANT' | 'INCONCLUSIVE';

/** One arm over every assigned customer, exposed or not (intent to treat). Decimals stay as the service sent them. */
export interface ArmOutcomeView {
  arm_id: number;
  kind: ArmKind;
  name: string;
  assigned: number;
  analysed: number;
  exposed: number;
  converted: number;
  /** No conversion yet, and the window is still open. */
  pending: number;
  not_converted: number;
  conversion_rate: string;
  /** Descriptive and non-causal: the exposed are a self-selected part of their arm. Absent for a control. */
  exposed_conversion_rate?: string;
  net_revenue: string;
  revenue_per_customer: string;
  revenue_std_dev: string;
}

export interface RevenueEffectView {
  amount: string;
  lower: string;
  upper: string;
}

export interface BootstrapView {
  resamples: number;
  lower: string;
  upper: string;
  agrees: boolean;
}

export interface ComparisonView {
  label: ComparisonLabel;
  metric: ExperimentMetric;
  role: MetricRole;
  arm_id: number;
  against_arm_id: number;
  difference: string;
  lower: string;
  upper: string;
  confidence_level: string;
  test: 'POOLED_Z' | 'WELCH_T';
  p_value: string;
  degrees_of_freedom?: string;
  /** On the primary comparison only. */
  conclusion?: Conclusion;
  /** On the primary comparison of an EFFECT whose metric is revenue per customer. */
  incremental_revenue?: RevenueEffectView;
  bootstrap?: BootstrapView;
}

export interface EffectRefusalView {
  code: string;
  message: string;
}

export interface ArmAttributionView {
  arm_id: number;
  attributed_sales: number;
  attributed_customers: number;
  attributed_revenue: string;
}

/** Commercial credit for sales, counted once. It is not an effect (ADR-0069). */
export interface AttributionView {
  label: 'ATTRIBUTION';
  window_days: number;
  arms: ArmAttributionView[];
  attributed_sales: number;
  attributed_revenue: string;
  unattributed_conversions: number;
  as_of: string;
}

export interface AssumptionView {
  /** An open vocabulary. */
  code: string;
  statement: string;
}

/** A result with the design it was planned under. Attribution and effect are separate sections and are never merged. */
export interface ExperimentResultView {
  experiment_id: number;
  campaign_id?: number;
  basis: ResultBasis;
  status: ResultStatus;
  as_of: string;
  /** The share of the conversion window elapsed at as_of, as sent. */
  elapsed_share: string;
  data_origin: ExperimentDataOrigin;
  /** True when the experiment is not OBSERVED or a conversion links a seeded sale: label the figures Synthetic. */
  synthetic: boolean;
  design_kind: ExperimentDesignKind;
  design: ExperimentDesignView;
  arms: ArmOutcomeView[];
  /** Absent while PENDING. */
  checks?: ExperimentChecksView;
  comparisons: ComparisonView[];
  effect_refusal?: EffectRefusalView;
  attribution?: AttributionView;
  assumptions: AssumptionView[];
  flags: ExperimentFlag[];
}

// ---------- K-means models (F28-05, #874) ----------

export type ModelMethod = 'RFM_RULES' | 'KMEANS';
export type VersionState = 'DRAFT' | 'PUBLISHED' | 'RETIRED';
export type VersionSource = 'REGISTERED' | 'TRAINING' | 'WEB_RUN';
export type TrainingState = 'RUNNING' | 'COMPLETED' | 'FAILED';
export type DataOrigin = 'OBSERVED' | 'SEEDED';
export type ModelFeature = 'R' | 'F' | 'M';
export type Normalization = 'NONE' | 'MIN_MAX';

/** What a version or a training was defined with. Decimals stay as model-registry sent them. */
export interface ModelParametersView {
  k?: number;
  max_iterations?: number;
  tolerance?: string;
  features: ModelFeature[];
  normalization: Normalization;
}

/** A coordinate of a centroid, in the normalized space the fit used. */
export interface CentroidView {
  cluster_index: number;
  label_code: string;
  r: string;
  f: string;
  m: string;
}

/** The raw minimum and maximum of min-max scaling, to put a centroid back into raw units. */
export interface BoundsView {
  r_min: string;
  r_max: string;
  f_min: string;
  f_max: string;
  m_min: string;
  m_max: string;
}

/** A label's share of the customers the version was fitted on (0 to 1, as sent). */
export interface LabelShareView {
  label_code: string;
  share: string;
}

export interface DatasetView {
  fingerprint: string;
  origin: DataOrigin;
  row_count: number;
}

/** A measure the method does not have is absent. */
export interface MetricsView {
  inertia?: string;
  silhouette?: string;
  stability?: string;
}

/** One immutable model version (ADR-0064). Only its state follows publications. */
export interface ModelVersionView {
  version_id: number;
  name: string;
  method: ModelMethod;
  state: VersionState;
  source: VersionSource;
  window_days: number;
  seed?: string;
  parameters: ModelParametersView;
  centroids: CentroidView[];
  bounds?: BoundsView;
  /** Customers the version was fitted on, the base of every label share. */
  sample_size: number;
  labels: LabelShareView[];
  dataset: DatasetView;
  metrics: MetricsView;
  training_id?: number;
  created_at: string;
  published_at?: string;
  retired_at?: string;
}

export interface TrainingView {
  training_id: number;
  state: TrainingState;
  version_name: string;
  window_days: number;
  /** Always set: the requested seed, or the one the service chose (RN-58). */
  seed: string;
  parameters: ModelParametersView;
  dataset?: DatasetView;
  metrics?: MetricsView;
  iterations?: number;
  converged?: boolean;
  /** The DRAFT version a COMPLETED training registered. */
  version_id?: number;
  started_at: string;
  finished_at?: string;
  failure_code?: string;
}

export interface PublicationView {
  publication_id: number;
  version_id: number;
  action: 'PUBLISH' | 'ROLLBACK';
  previous_version_id?: number;
  reason: string;
  published_at: string;
}

export interface ModelVersionQuery {
  method?: ModelMethod;
  state?: VersionState;
  page?: number;
  size?: number;
}

export interface TrainingQuery {
  state?: TrainingState;
  page?: number;
  size?: number;
}

/** What a K-means training is started with. An absent seed is chosen and recorded by the service. */
export interface TrainingDraft {
  version_name: string;
  window_days: number;
  k: number;
  max_iterations: number;
  tolerance: string;
  features: ModelFeature[];
  normalization: Normalization;
  /** Whole number as text, because a 64-bit seed does not fit a JavaScript number. */
  seed?: string;
}

/** The named operations the renderer may call; there is no generic transport. */
export interface MosaiqBridge {
  getState(): Promise<AppState>;
  signIn(credentials: { email: string; password: string }): Promise<ActionResult>;
  signOut(): Promise<ActionResult>;
  probeService(service: string): Promise<ProbeResult>;
  exportTable(request: ExportRequest): Promise<Result<ExportOutcome>>;
  onStateChange(listener: (state: AppState) => void): () => void;
  listProducts(query: ProductQuery): Promise<Result<Paged<ProductView>>>;
  getProduct(productId: number): Promise<Result<ProductView>>;
  createProduct(draft: ProductDraft): Promise<Result<ProductView>>;
  updateProduct(productId: number, changes: ProductChanges): Promise<Result<ProductView>>;
  deactivateProduct(productId: number): Promise<Result<ProductView>>;
  categoryTree(): Promise<Result<CategoryNode[]>>;
  listCategories(parentId: number | null, page: number): Promise<Result<Paged<CategoryView>>>;
  categoryDescendants(categoryId: number, page: number): Promise<Result<Paged<CategoryView>>>;
  createCategory(draft: CategoryDraft): Promise<Result<CategoryView>>;
  updateCategory(categoryId: number, changes: CategoryChanges): Promise<Result<CategoryView>>;
  deleteCategory(categoryId: number): Promise<Result<null>>;
  stores(): Promise<Result<StoreView[]>>;
  listStores(query: StoreQuery): Promise<Result<Paged<StoreDetailView>>>;
  getStore(storeId: number): Promise<Result<StoreDetailView>>;
  setStoreLocation(storeId: number, location: StoreLocationDraft): Promise<Result<StoreDetailView>>;
  removeStoreLocation(storeId: number): Promise<Result<null>>;
  listCampaigns(query: CampaignQuery): Promise<Result<Paged<CampaignView>>>;
  getCampaign(campaignId: number): Promise<Result<CampaignView>>;
  createCampaign(draft: CampaignDraft): Promise<Result<CampaignView>>;
  updateCampaign(campaignId: number, changes: CampaignChanges): Promise<Result<CampaignView>>;
  /** expected_status is the status the screen displayed; the service refuses a stale one. */
  transitionCampaign(
    campaignId: number,
    to: CampaignStatus,
    expectedStatus: CampaignStatus,
  ): Promise<Result<CampaignView>>;
  listCampaignLabels(): Promise<Result<LabelView[]>>;
  getCampaignAudience(campaignId: number): Promise<Result<AudienceView>>;
  putCampaignAudience(campaignId: number, draft: AudienceDraft): Promise<Result<AudienceView>>;
  getCampaignBudget(campaignId: number): Promise<Result<BudgetView>>;
  putCampaignBudget(campaignId: number, draft: BudgetDraft): Promise<Result<BudgetView>>;
  getCampaignEligibilityRules(campaignId: number): Promise<Result<EligibilityView>>;
  putCampaignEligibilityRules(campaignId: number, draft: EligibilityDraft): Promise<Result<EligibilityView>>;
  getCampaignExclusions(campaignId: number): Promise<Result<ExclusionsView>>;
  putCampaignExclusions(campaignId: number, draft: ExclusionsDraft): Promise<Result<ExclusionsView>>;
  previewCampaignAudience(campaignId: number): Promise<Result<AudiencePreviewView>>;
  listAuditEntries(query: AuditQuery): Promise<Result<Paged<AuditEntryView>>>;
  getAuditEntry(auditId: string): Promise<Result<AuditEntryView>>;
  stockLevels(query: StockQuery): Promise<Result<Paged<StockLevelView>>>;
  listSales(query: SalesQuery): Promise<Result<Paged<SaleView>>>;
  getSale(saleId: number): Promise<Result<SaleView>>;
  engagementSummary(query: SummaryQuery): Promise<Result<SummaryView>>;
  listEngagementEvents(query: EventsQuery): Promise<Result<Paged<EngagementEventView>>>;
  voidEngagementEvent(eventId: string, reason: string): Promise<Result<EngagementEventView>>;
  listCheckIns(query: CheckInsQuery): Promise<Result<Paged<CheckInView>>>;
  getCheckIn(checkInId: string): Promise<Result<CheckInDetailView>>;
  reviewCheckIn(checkInId: string, verified: boolean, reason: string): Promise<Result<ReviewView>>;
  listUsers(query: UserQuery): Promise<Result<Paged<UserView>>>;
  getUser(userId: string): Promise<Result<UserView>>;
  listRoles(): Promise<Result<RoleView[]>>;
  createUser(draft: UserDraft): Promise<Result<UserView>>;
  updateUser(userId: string, changes: UserChanges): Promise<Result<UserView>>;
  deactivateUser(userId: string): Promise<Result<UserView>>;
  reactivateUser(userId: string): Promise<Result<UserView>>;
  /** An administrator sets another user's password; no current password is sent. */
  resetUserPassword(userId: string, newPassword: string): Promise<Result<null>>;
  /** The caller's own password. A success ends every session, so the app returns to sign-in. */
  changeOwnPassword(currentPassword: string, newPassword: string): Promise<Result<null>>;
  transferAdministrator(successorUserId: string): Promise<Result<null>>;
  searchCustomers(query: CustomerQuery): Promise<Result<Paged<CustomerView>>>;
  getCustomer(customerId: string): Promise<Result<CustomerView>>;
  correctCustomer(customerId: string, changes: CustomerChanges): Promise<Result<CustomerView>>;
  /** Every channel of sales-service, for the registration channel. */
  listChannels(): Promise<Result<ChoiceView[]>>;
  getPreferences(customerId: string): Promise<Result<PreferencesView>>;
  /** Sends the whole set; an empty list clears it. */
  replacePreferredChannels(customerId: string, channelIds: number[]): Promise<Result<PreferencesView>>;
  replaceInterestCategories(customerId: string, categoryIds: number[]): Promise<Result<PreferencesView>>;
  segmentVocabulary(): Promise<Result<SegmentVocabularyView>>;
  currentAssignment(customerId: string): Promise<Result<CurrentAssignmentView>>;
  listAssignments(query: AssignmentQuery): Promise<Result<Paged<AssignmentRowView>>>;
  getRecommendations(customerId: string): Promise<Result<RecommendationView>>;
  kpiDefinitions(): Promise<Result<KpiDefinitionView[]>>;
  kpiReport(query: ReportQuery): Promise<Result<ReportView>>;
  exportReport(format: ReportFormat): Promise<Result<ExportOutcome>>;
  inspectRecommendation(query: InspectQuery): Promise<Result<InspectionView>>;
  listRecommendationDecisions(query: DecisionQuery): Promise<Result<Paged<DecisionSummaryView>>>;
  openRecommendationDecision(decisionId: string): Promise<Result<InspectionView>>;
  listDriftReports(query: DriftReportQuery): Promise<Result<Paged<DriftReportView>>>;
  getDriftReport(reportId: number): Promise<Result<DriftReportView>>;
  listDriftAlerts(query: DriftAlertQuery): Promise<Result<Paged<DriftAlertView>>>;
  getDriftAlert(alertId: number): Promise<Result<DriftAlertView>>;
  migrationMatrix(query: MigrationWindow): Promise<Result<MigrationMatrixView>>;
  listMigrations(query: MigrationListQuery): Promise<Result<MigrationListView>>;
  explainMigration(query: MigrationExplanationQuery): Promise<Result<MigrationExplanationView>>;
  exploreProfile(customerId: string): Promise<Result<ExplorationProfileView>>;
  exploreSales(query: ExplorationQuery): Promise<Result<ExplorationSalesView>>;
  exploreRestrictions(customerId: string): Promise<Result<RestrictionsView>>;
  exploreConsent(customerId: string): Promise<Result<ConsentView>>;
  exploreInterests(customerId: string): Promise<Result<InterestsView>>;
  listExperiments(query: ExperimentQuery): Promise<Result<Paged<ExperimentView>>>;
  getExperiment(experimentId: number): Promise<Result<ExperimentView>>;
  planExperiment(draft: ExperimentDraft): Promise<Result<ExperimentView>>;
  assignExperiment(experimentId: number, acknowledgeUnderpowered: boolean): Promise<Result<AssignmentView>>;
  getExperimentResult(experimentId: number, basis: ResultBasis): Promise<Result<ExperimentResultView>>;
  listModelVersions(query: ModelVersionQuery): Promise<Result<Paged<ModelVersionView>>>;
  getModelVersion(versionId: number): Promise<Result<ModelVersionView>>;
  listTrainings(query: TrainingQuery): Promise<Result<Paged<TrainingView>>>;
  getTraining(trainingId: number): Promise<Result<TrainingView>>;
  /** Starts a K-means training; the answer is the run, RUNNING, and its result becomes a DRAFT version. */
  startTraining(draft: TrainingDraft): Promise<Result<TrainingView>>;
  /** Publishes a DRAFT with the reason that is kept in the append-only publication. */
  publishModelVersion(versionId: number, reason: string): Promise<Result<PublicationView>>;
}

export const CHANNELS = {
  getState: 'mosaiq:get-state',
  signIn: 'mosaiq:sign-in',
  signOut: 'mosaiq:sign-out',
  probeService: 'mosaiq:probe-service',
  stateChanged: 'mosaiq:state-changed',
  exportTable: 'mosaiq:export-table',
  listProducts: 'mosaiq:list-products',
  getProduct: 'mosaiq:get-product',
  createProduct: 'mosaiq:create-product',
  updateProduct: 'mosaiq:update-product',
  deactivateProduct: 'mosaiq:deactivate-product',
  categoryTree: 'mosaiq:category-tree',
  listCategories: 'mosaiq:list-categories',
  categoryDescendants: 'mosaiq:category-descendants',
  createCategory: 'mosaiq:create-category',
  updateCategory: 'mosaiq:update-category',
  deleteCategory: 'mosaiq:delete-category',
  stores: 'mosaiq:stores',
  listStores: 'mosaiq:list-stores',
  getStore: 'mosaiq:get-store',
  setStoreLocation: 'mosaiq:set-store-location',
  removeStoreLocation: 'mosaiq:remove-store-location',
  listCampaigns: 'mosaiq:list-campaigns',
  getCampaign: 'mosaiq:get-campaign',
  createCampaign: 'mosaiq:create-campaign',
  updateCampaign: 'mosaiq:update-campaign',
  transitionCampaign: 'mosaiq:transition-campaign',
  listCampaignLabels: 'mosaiq:list-campaign-labels',
  getCampaignAudience: 'mosaiq:get-campaign-audience',
  putCampaignAudience: 'mosaiq:put-campaign-audience',
  getCampaignBudget: 'mosaiq:get-campaign-budget',
  putCampaignBudget: 'mosaiq:put-campaign-budget',
  getCampaignEligibilityRules: 'mosaiq:get-campaign-eligibility-rules',
  putCampaignEligibilityRules: 'mosaiq:put-campaign-eligibility-rules',
  getCampaignExclusions: 'mosaiq:get-campaign-exclusions',
  putCampaignExclusions: 'mosaiq:put-campaign-exclusions',
  previewCampaignAudience: 'mosaiq:preview-campaign-audience',
  listAuditEntries: 'mosaiq:list-audit-entries',
  getAuditEntry: 'mosaiq:get-audit-entry',
  stockLevels: 'mosaiq:stock-levels',
  listSales: 'mosaiq:list-sales',
  getSale: 'mosaiq:get-sale',
  engagementSummary: 'mosaiq:engagement-summary',
  listEngagementEvents: 'mosaiq:list-engagement-events',
  voidEngagementEvent: 'mosaiq:void-engagement-event',
  listCheckIns: 'mosaiq:list-check-ins',
  getCheckIn: 'mosaiq:get-check-in',
  reviewCheckIn: 'mosaiq:review-check-in',
  listUsers: 'mosaiq:list-users',
  getUser: 'mosaiq:get-user',
  listRoles: 'mosaiq:list-roles',
  createUser: 'mosaiq:create-user',
  updateUser: 'mosaiq:update-user',
  deactivateUser: 'mosaiq:deactivate-user',
  reactivateUser: 'mosaiq:reactivate-user',
  resetUserPassword: 'mosaiq:reset-user-password',
  changeOwnPassword: 'mosaiq:change-own-password',
  transferAdministrator: 'mosaiq:transfer-administrator',
  searchCustomers: 'mosaiq:search-customers',
  getCustomer: 'mosaiq:get-customer',
  correctCustomer: 'mosaiq:correct-customer',
  listChannels: 'mosaiq:list-channels',
  getPreferences: 'mosaiq:get-preferences',
  replacePreferredChannels: 'mosaiq:replace-preferred-channels',
  replaceInterestCategories: 'mosaiq:replace-interest-categories',
  segmentVocabulary: 'mosaiq:segment-vocabulary',
  currentAssignment: 'mosaiq:current-assignment',
  listAssignments: 'mosaiq:list-assignments',
  getRecommendations: 'mosaiq:get-recommendations',
  kpiDefinitions: 'mosaiq:kpi-definitions',
  kpiReport: 'mosaiq:kpi-report',
  exportReport: 'mosaiq:export-report',
  inspectRecommendation: 'mosaiq:inspect-recommendation',
  listRecommendationDecisions: 'mosaiq:list-recommendation-decisions',
  openRecommendationDecision: 'mosaiq:open-recommendation-decision',
  listDriftReports: 'mosaiq:list-drift-reports',
  getDriftReport: 'mosaiq:get-drift-report',
  listDriftAlerts: 'mosaiq:list-drift-alerts',
  getDriftAlert: 'mosaiq:get-drift-alert',
  migrationMatrix: 'mosaiq:migration-matrix',
  listMigrations: 'mosaiq:list-migrations',
  explainMigration: 'mosaiq:explain-migration',
  exploreProfile: 'mosaiq:explore-profile',
  exploreSales: 'mosaiq:explore-sales',
  exploreRestrictions: 'mosaiq:explore-restrictions',
  exploreConsent: 'mosaiq:explore-consent',
  exploreInterests: 'mosaiq:explore-interests',
  listExperiments: 'mosaiq:list-experiments',
  getExperiment: 'mosaiq:get-experiment',
  planExperiment: 'mosaiq:plan-experiment',
  assignExperiment: 'mosaiq:assign-experiment',
  getExperimentResult: 'mosaiq:get-experiment-result',
  listModelVersions: 'mosaiq:list-model-versions',
  getModelVersion: 'mosaiq:get-model-version',
  listTrainings: 'mosaiq:list-trainings',
  getTraining: 'mosaiq:get-training',
  startTraining: 'mosaiq:start-training',
  publishModelVersion: 'mosaiq:publish-model-version',
} as const;
