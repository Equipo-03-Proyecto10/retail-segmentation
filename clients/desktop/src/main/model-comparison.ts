import type { ModelComparisonView, Result, VersionAssignmentView } from '../shared/api';
import { compareModelAssignments } from '../shared/model-comparison';
import { localError, type Backoffice } from './backoffice';
import { checked, failed, id } from './checks';
import type { Models } from './models';

// F28-13 (#882): compose immutable versions from model-registry with their assignment rows from
// segments. Both services answer XML and Backoffice validates each response against its own XSD.
// The newest row per customer under a version is its comparison state; older incremental rows do
// not count the same customer twice.

const MAX_ID = Number.MAX_SAFE_INTEGER;
const PAGE_SIZE = 100;

interface AssignmentXml {
  customer_id: string;
  label_code?: string;
}

interface PageXml {
  VersionedSegmentAssignment?: AssignmentXml[];
  PageInfo: { page: string; size: string; total: string };
}

export class ModelComparison {
  constructor(
    private readonly office: Backoffice,
    private readonly models: Models,
  ) {}

  private mayRead(): Result<never> | null {
    const state = this.office.state();
    if (
      state.status === 'signed_in' &&
      state.operations.includes('getModelVersion') &&
      state.operations.includes('listVersionedSegmentAssignments')
    )
      return null;
    if (state.status !== 'signed_in')
      return { ok: false, error: localError('signed_out', 'You are signed out.') };
    return {
      ok: false,
      error: localError('forbidden_role', 'Your role may not compare model versions.'),
    };
  }

  private async assignments(versionId: number): Promise<Result<VersionAssignmentView[]>> {
    const newest = new Map<string, VersionAssignmentView>();
    let page = 1;
    let consumed = 0;
    let hasMore = true;
    do {
      const params = new URLSearchParams({
        model_version_id: String(versionId),
        page: String(page),
        size: String(PAGE_SIZE),
      });
      const res = await this.office.authed({
        method: 'GET',
        path: `/v1/segment-assignments/versioned?${params.toString()}`,
      });
      if (!res.ok) return failed(res);
      const data = res.data?.VersionedSegmentAssignments as PageXml;
      const rows = data.VersionedSegmentAssignment ?? [];
      for (const row of rows)
        if (!newest.has(row.customer_id))
          newest.set(row.customer_id, {
            customer_id: row.customer_id,
            ...(row.label_code === undefined ? {} : { label_code: row.label_code }),
          });
      consumed += rows.length;
      hasMore = consumed < Number(data.PageInfo.total) && rows.length > 0;
      if (hasMore) page += 1;
    } while (hasMore);
    return { ok: true, value: [...newest.values()] };
  }

  compare(firstVersionId: unknown, secondVersionId: unknown): Promise<Result<ModelComparisonView>> {
    return checked(this.office, async () => {
      const refused = this.mayRead();
      if (refused !== null) return refused;
      const firstId = id(firstVersionId, MAX_ID, 'The first version');
      const secondId = id(secondVersionId, MAX_ID, 'The second version');
      if (firstId === secondId)
        return {
          ok: false,
          error: localError('invalid_input', 'Choose two different model versions.'),
        };
      const [first, second] = await Promise.all([
        this.models.getVersion(firstId),
        this.models.getVersion(secondId),
      ]);
      if (!first.ok) return first;
      if (!second.ok) return second;
      const [firstRows, secondRows] = await Promise.all([
        this.assignments(firstId),
        this.assignments(secondId),
      ]);
      if (!firstRows.ok) return firstRows;
      if (!secondRows.ok) return secondRows;
      return {
        ok: true,
        value: compareModelAssignments(first.value, second.value, firstRows.value, secondRows.value),
      };
    });
  }
}
