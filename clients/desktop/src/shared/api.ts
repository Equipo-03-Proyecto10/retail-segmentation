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
  stockLevels(query: StockQuery): Promise<Result<Paged<StockLevelView>>>;
  listSales(query: SalesQuery): Promise<Result<Paged<SaleView>>>;
  getSale(saleId: number): Promise<Result<SaleView>>;
  engagementSummary(query: SummaryQuery): Promise<Result<SummaryView>>;
  listEngagementEvents(query: EventsQuery): Promise<Result<Paged<EngagementEventView>>>;
  voidEngagementEvent(eventId: string, reason: string): Promise<Result<EngagementEventView>>;
  listCheckIns(query: CheckInsQuery): Promise<Result<Paged<CheckInView>>>;
  getCheckIn(checkInId: string): Promise<Result<CheckInDetailView>>;
  reviewCheckIn(checkInId: string, verified: boolean, reason: string): Promise<Result<ReviewView>>;
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
  stockLevels: 'mosaiq:stock-levels',
  listSales: 'mosaiq:list-sales',
  getSale: 'mosaiq:get-sale',
  engagementSummary: 'mosaiq:engagement-summary',
  listEngagementEvents: 'mosaiq:list-engagement-events',
  voidEngagementEvent: 'mosaiq:void-engagement-event',
  listCheckIns: 'mosaiq:list-check-ins',
  getCheckIn: 'mosaiq:get-check-in',
  reviewCheckIn: 'mosaiq:review-check-in',
} as const;
