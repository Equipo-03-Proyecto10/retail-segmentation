// Types shared by the main process, the preload bridge and the screens.
// Nothing in here carries a credential.

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
    };

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

/** The named operations the renderer may call; there is no generic transport. */
export interface MosaiqBridge {
  getState(): Promise<AppState>;
  signIn(credentials: { email: string; password: string }): Promise<ActionResult>;
  signOut(): Promise<ActionResult>;
  probeService(service: string): Promise<ProbeResult>;
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
}

export const CHANNELS = {
  getState: 'mosaiq:get-state',
  signIn: 'mosaiq:sign-in',
  signOut: 'mosaiq:sign-out',
  probeService: 'mosaiq:probe-service',
  stateChanged: 'mosaiq:state-changed',
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
} as const;
