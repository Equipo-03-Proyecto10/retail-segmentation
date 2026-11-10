// The console's modules, grouped by the work they serve (F28-01, #870). Pure data and rules, so
// the main process and the renderer apply the same ones and a unit test can pin them.

export type ModuleName =
  | 'products'
  | 'categories'
  | 'stock'
  | 'sales'
  | 'engagement'
  | 'check-ins'
  | 'reports'
  | 'stores'
  | 'campaigns'
  | 'experiments'
  | 'customers'
  | 'users'
  | 'audit'
  | 'transfer'
  | 'services';

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
      { name: 'sales', label: 'Sales', operation: 'listSales' },
      { name: 'engagement', label: 'Engagement', operation: 'getEngagementSummary' },
      { name: 'check-ins', label: 'Check-ins', operation: 'listCheckIns' },
      { name: 'reports', label: 'Reports', operation: 'listKpiDefinitions' },
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

/** The operations the A/B experiment module calls (experimentation x-mosaiq-roles); each is checked before its call. */
export const EXPERIMENT_OPERATIONS = {
  list: 'listExperiments',
  get: 'getExperiment',
  plan: 'planExperiment',
  assign: 'assignExperiment',
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
