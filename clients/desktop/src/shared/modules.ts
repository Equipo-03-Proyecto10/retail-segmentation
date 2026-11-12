// The console's modules, grouped by the work they serve (F28-01, #870). Pure data and rules, so
// the main process and the renderer apply the same ones and a unit test can pin them.

export type ModuleName =
  | 'products'
  | 'categories'
  | 'stock'
  | 'sales'
  | 'engagement'
  | 'check-ins'
  | 'migrations'
  | 'reports'
  | 'recommendations'
  | 'drift'
  | 'kmeans'
  | 'stores'
  | 'campaigns'
  | 'experiments'
  | 'conversion'
  | 'customers'
  | 'exploration'
  | 'users'
  | 'audit'
  | 'transfer'
  | 'services'
  | 'imports'
  | 'inventory';

export interface ConsoleModule {
  name: ModuleName;
  label: string;
  /** The read operation a role must be allowed to call (x-mosaiq-roles); null needs no operation. */
  operation: string | null;
}

export interface ModuleGroup {
  id: 'analysis' | 'marketing' | 'planning' | 'administration';
  label: string;
  modules: ConsoleModule[];
}

export const MODULE_GROUPS: readonly ModuleGroup[] = [
  {
    id: 'analysis',
    label: 'Analysis',
    modules: [
      { name: 'customers', label: 'Customers', operation: 'searchCustomers' },
      { name: 'exploration', label: 'Customer exploration', operation: 'getRfmProfile' },
      { name: 'sales', label: 'Sales', operation: 'listSales' },
      { name: 'engagement', label: 'Engagement', operation: 'getEngagementSummary' },
      { name: 'check-ins', label: 'Check-ins', operation: 'listCheckIns' },
      { name: 'migrations', label: 'Migration matrix', operation: 'getSegmentMigrationMatrix' },
      { name: 'reports', label: 'Reports', operation: 'listKpiDefinitions' },
      { name: 'recommendations', label: 'Recommendations', operation: 'getRecommendationsV2' },
      { name: 'drift', label: 'Drift', operation: 'listDriftReports' },
      { name: 'conversion', label: 'Conversion analysis', operation: 'getExperimentResult' },
      { name: 'kmeans', label: 'K-means', operation: 'listModelVersions' },
      { name: 'imports', label: 'Import and data quality', operation: 'listSaleImports' },
    ],
  },
  {
    id: 'marketing',
    label: 'Marketing',
    modules: [
      { name: 'campaigns', label: 'Campaigns', operation: 'listCampaigns' },
      { name: 'experiments', label: 'A/B experiments', operation: 'listExperiments' },
    ],
  },
  {
    id: 'planning',
    label: 'Planning',
    modules: [
      { name: 'products', label: 'Products', operation: 'listProducts' },
      { name: 'categories', label: 'Categories', operation: 'listCategories' },
      { name: 'stock', label: 'Stock', operation: 'listStockLevels' },
      { name: 'stores', label: 'Stores', operation: 'listStores' },
      { name: 'inventory', label: 'Demand and stock', operation: 'getDemandReport' },
    ],
  },
  {
    id: 'administration',
    label: 'Administration',
    modules: [
      { name: 'users', label: 'Users', operation: 'listUsers' },
      { name: 'audit', label: 'Audit trail', operation: 'listAuditEntries' },
      { name: 'transfer', label: 'Transfer administrator', operation: 'transferAdministrator' },
      { name: 'services', label: 'Services', operation: null },
    ],
  },
];

/** The groups a role sees: only the modules whose operation it may call, and no empty group. */
export function visibleGroups(operations: readonly string[]): ModuleGroup[] {
  return MODULE_GROUPS.map((g) => ({
    ...g,
    modules: g.modules.filter((m) => m.operation === null || operations.includes(m.operation)),
  })).filter((g) => g.modules.length > 0);
}

/** Roles that may write a table to a file (ADR-0074). STORE_MANAGER works the floor and may not. */
export const EXPORT_ROLES: readonly string[] = ['ADMIN', 'ANALYST', 'MARKETING', 'AUDITOR'];

export function mayExport(role: string): boolean {
  return EXPORT_ROLES.includes(role);
}

/** The read operation of each KPI report (analytics-kpis x-mosaiq-roles); a role sees a KPI only if it may call this. */
export const KPI_OPERATIONS: Readonly<Record<string, string>> = {
  'net-sales': 'getNetSalesReport',
  spend: 'getSpendReport',
  retention: 'getRetentionReport',
  migrations: 'getMigrationReport',
  conversion: 'getConversionReport',
  'recommendation-performance': 'getRecommendationPerformanceReport',
  'campaign-performance': 'getCampaignPerformanceReport',
};

/**
 * The read operations the recommendations module calls (recommendations x-mosaiq-roles); each is checked before
 * its call. Decisions are open to fewer roles than the recommendation itself.
 */
export const RECOMMENDATION_OPERATIONS = {
  inspect: 'getRecommendationsV2',
  decisions: 'listRecommendationDecisions',
  decision: 'getRecommendationDecision',
} as const;

/** The read operations the drift module calls (model-registry x-mosaiq-roles); each is checked before its call. */
export const DRIFT_OPERATIONS = {
  reports: 'listDriftReports',
  report: 'getDriftReport',
  alerts: 'listDriftAlerts',
  alert: 'getDriftAlert',
} as const;

/** The read operations the migration matrix module calls (segments x-mosaiq-roles); each is checked before its call. */
export const MIGRATION_OPERATIONS = {
  matrix: 'getSegmentMigrationMatrix',
  list: 'listSegmentMigrations',
  explanation: 'getSegmentMigrationExplanation',
} as const;

/** The operations the A/B experiment module calls (experimentation x-mosaiq-roles); each is checked before its call. */
export const EXPERIMENT_OPERATIONS = {
  list: 'listExperiments',
  get: 'getExperiment',
  plan: 'planExperiment',
  assign: 'assignExperiment',
} as const;

/** The operation the conversion analysis module calls (experimentation x-mosaiq-roles); it is checked before the call. */
export const CONVERSION_OPERATIONS = { result: 'getExperimentResult' } as const;

/** The read operations the audit module calls (audit-trail x-mosaiq-roles: ADMIN, AUDITOR); each is checked before its call. */
export const AUDIT_OPERATIONS = {
  entries: 'listAuditEntries',
  entry: 'getAuditEntry',
  events: 'listDomainEvents',
  event: 'getDomainEvent',
} as const;

/** The read operations the inventory planning module calls (analytics-kpis and stock x-mosaiq-roles); each is checked before its call (ADR-0079). */
export const INVENTORY_OPERATIONS = {
  demand: 'getDemandReport',
  stock: 'listStockLevels',
  stores: 'listStores',
  products: 'listProducts',
} as const;

/** Error codes that mean the session is over; the user goes back to sign-in. */
export const SESSION_ENDED_CODES: readonly string[] = [
  'signed_out',
  'session_expired',
  'session_revoked',
  'invalid_refresh',
  'refresh_reused',
];

export function endsSession(code: string): boolean {
  return SESSION_ENDED_CODES.includes(code);
}
