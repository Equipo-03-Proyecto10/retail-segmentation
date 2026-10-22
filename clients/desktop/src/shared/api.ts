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
} as const;
