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
  'Campaigns.Campaign',
  'CampaignAudienceBreakdown.reasons',
  'CampaignExclusions.customer_ids',
  'CampaignExclusions.label_codes',
  'Segments.Segment',
  'AuditEntries.AuditEntry',
  'Customers.Customer',
  'SegmentAssignments.SegmentAssignment',
  'PreferenceProfile.preferred_channel_ids',
  'PreferenceProfile.interest_category_ids',
  'Recommendation.items',
  'Recommendation.items.reasons',
  'Recommendation.exclusions',
  'RecommendationDecision.exclusions',
  'RecommendationDecision.items',
  'RecommendationDecision.items.reasons',
  'RecommendationDecisions.RecommendationDecisionSummary',
  'RecommendationDecisions.RecommendationDecisionSummary.exclusions',
  'KpiReport.group_by',
  'KpiReport.rows',
  'KpiReport.rows.measures',
  'KpiReport.data_origin.views',
  'KpiDefinitions.KpiDefinition',
  'KpiDefinitions.KpiDefinition.dimensions',
  'KpiDefinitions.KpiDefinition.measures',
  'DemandCoverageReport.rows',
  'DemandCoverageReport.rows.segments',
  'DemandCoverageReport.data_origin.views',
  'DomainEvents.DomainEvent',
  'DomainEvents.DomainEvent.details.PermissionChange.store_id',
  'DomainEvent.details.PermissionChange.store_id',
  'DomainEvents.DomainEvent.details.AuditRead.filter',
  'DomainEvent.details.AuditRead.filter',
  'SaleImports.SaleImport',
  'SaleImportRejections.SaleImportRejection',
  'DriftReport.variables',
  'DriftReport.variables.statistics',
  'DriftReport.variables.bins',
  'DriftReport.outcome_test.segments',
  'DriftReport.alert_ids',
  'DriftReports.DriftReport',
  'DriftReports.DriftReport.variables',
  'DriftReports.DriftReport.variables.statistics',
  'DriftReports.DriftReport.variables.bins',
  'DriftReports.DriftReport.outcome_test.segments',
  'DriftReports.DriftReport.alert_ids',
  'DriftAlert.statistics',
  'DriftAlert.bins',
  'DriftAlert.outcome_test.segments',
  'DriftAlerts.DriftAlert',
  'DriftAlerts.DriftAlert.statistics',
  'DriftAlerts.DriftAlert.bins',
  'DriftAlerts.DriftAlert.outcome_test.segments',
  'SegmentMigrationMatrix.MigrationTotal',
  'SegmentMigrationMatrix.MigrationCell',
  'SegmentMigrations.SegmentMigration',
  'SegmentMigrations.SegmentMigration.responsible_variables',
  'SegmentMigrationExplanation.migration.responsible_variables',
  'SegmentMigrationExplanation.MigrationMeasure',
  'SaleReversals.SaleReversal',
  'RestrictionProfile.restricted_channel_ids',
  'RestrictionProfile.restricted_category_ids',
  'ConsentState.purposes',
  'InterestProfile.declared_category_ids',
  'InterestProfile.inferred_interests',
  'ModelVersions.ModelVersion',
  'ModelVersions.ModelVersion.parameters.features',
  'ModelVersions.ModelVersion.frozen_scoring.centroids',
  'ModelVersions.ModelVersion.reference_distribution.labels',
  'ModelVersion.parameters.features',
  'ModelVersion.frozen_scoring.centroids',
  'ModelVersion.reference_distribution.labels',
  'ModelTrainings.ModelTraining',
  'ModelTrainings.ModelTraining.parameters.features',
  'ModelTraining.parameters.features',
  'ModelPublications.ModelPublication',
  'Experiments.Experiment',
  'Experiments.Experiment.arms',
  'Experiments.Experiment.flags',
  'Experiments.Experiment.frame.exclusions',
  'Experiments.Experiment.assignment.arms',
  'Experiments.Experiment.assignment.checks.balance',
  'Experiment.arms',
  'Experiment.flags',
  'Experiment.frame.exclusions',
  'Experiment.assignment.arms',
  'Experiment.assignment.checks.balance',
  'AssignmentSummary.arms',
  'AssignmentSummary.checks.balance',
  'ExperimentResult.arms',
  'ExperimentResult.checks.balance',
  'ExperimentResult.comparisons',
  'ExperimentResult.attribution.arms',
  'ExperimentResult.assumptions',
  'ExperimentResult.flags',
]);
/** Child elements that repeat wherever they appear: error details, sale lines, summary groups, event history, check-in reviews, a campaign's allowed transitions. */
const REPEATED_CHILDREN = new Set([
  'details',
  'lines',
  'groups',
  'history',
  'reviews',
  'allowed_transitions',
]);

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

/**
 * A request body's fields, in order. A list of text repeats its element once per value, and an
 * empty list sends none. A nested record is a child element with children of its own. A list of
 * records repeats the element once per record, each with its own children (the arms of a plan,
 * the rows of a chunk); an empty string is sent as an empty element, so a cell keeps its position.
 */
export interface Fields {
  [name: string]: FieldValue | string[] | Fields | Fields[];
}

/** Fills an element from fields, in order; true when any field is an explicit xsi:nil. */
function fill(element: Record<string, unknown>, fields: Fields): boolean {
  let nil = false;
  for (const [name, value] of Object.entries(fields)) {
    if (Array.isArray(value)) {
      if (value.length === 0) continue;
      element[name] = (value as (string | Fields)[]).map((item) => {
        if (typeof item === 'string') return item;
        const child: Record<string, unknown> = {};
        nil = fill(child, item) || nil;
        return child;
      });
    } else if (value === null) {
      element[name] = { '@_xsi:nil': 'true' };
      nil = true;
    } else if (typeof value === 'object') {
      const child: Record<string, unknown> = {};
      nil = fill(child, value) || nil;
      element[name] = child;
    } else {
      element[name] = value;
    }
  }
  return nil;
}

/** Builds a request body from its fields, in the given order. */
export function buildXml(root: string, namespace: string, fields: Fields): string {
  const element: Record<string, unknown> = { '@_xmlns': namespace };
  const nil = fill(element, fields);
  if (nil) element['@_xmlns:xsi'] = XSI;
  const body = builder.build({ [root]: element }) as string;
  return `<?xml version="1.0" encoding="UTF-8"?>${body}`;
}
