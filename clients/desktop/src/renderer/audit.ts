import type { AuditEntryView, AuditQuery } from '../shared/api';
import { AUDIT_ACTIONS, AUDITED_ENTITIES } from '../shared/api';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, formField, pager, select, testid, textInput } from './dom';
import { latestRequest } from './latest-request';

// The audit trail (F16-09, #487). Read only: the trail is append-only, so the
// screen draws no create, edit or delete control. The acting user is shown by
// its user_id. Payloads are JSON text; they are formatted and set with
// textContent, never as HTML.

const SIZES: [string, string][] = [
  ['20', '20 a page'],
  ['50', '50 a page'],
  ['100', '100 a page'],
];

/** The payload as indented JSON when it parses, as returned otherwise. */
export function readable(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

export function renderAudit(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const tableArea = el('div');
  const detail = testid(el('section', { cls: 'panel' }), 'audit-detail');
  const renderLatestList = latestRequest();
  const renderLatestDetail = latestRequest();
  let query: AuditQuery = { page: 1, size: 20 };

  const entity = select([['', 'Any entity'], ...AUDITED_ENTITIES.map((e): [string, string] => [e, e])], '');
  const entityPk = textInput('');
  const userId = textInput('');
  const action = select([['', 'Any action'], ...AUDIT_ACTIONS.map((a): [string, string] => [a, a])], '');
  const from = textInput('');
  const to = textInput('');
  from.placeholder = to.placeholder = '2026-10-16T14:00:00Z';
  const size = select(SIZES, '20');
  const search = el('button', { text: 'Search' });
  search.type = 'submit';
  const filters = testid(
    el(
      'form',
      { cls: 'filters' },
      formField('entity', 'Entity', entity, [], 'filter'),
      formField('entity_pk', 'Entity key', entityPk, [], 'filter'),
      formField('user_id', 'User id', userId, [], 'filter'),
      formField('action', 'Action', action, [], 'filter'),
      formField('from', 'From (RFC 3339)', from, [], 'filter'),
      formField('to', 'To (RFC 3339)', to, [], 'filter'),
      formField('size', 'Page size', size, [], 'filter'),
      search,
    ),
    'audit-filters',
  );
  filters.noValidate = true;
  filters.addEventListener('submit', (event) => {
    event.preventDefault();
    query = { page: 1, size: Number(size.value) };
    if (entity.value) query.entity = entity.value;
    if (entityPk.value.trim()) query.entity_pk = entityPk.value.trim();
    if (userId.value.trim()) query.user_id = userId.value.trim();
    if (action.value) query.action = action.value;
    if (from.value.trim()) query.from = from.value.trim();
    if (to.value.trim()) query.to = to.value.trim();
    void load();
  });

  async function load(): Promise<void> {
    await renderLatestList(
      () => api.listAuditEntries(query),
      (res) => {
        // A refusal (invalid_query, forbidden_role) is shown as returned, with each detail.
        if (!res.ok) return void tableArea.replaceChildren(errorBox(res.error));
        const head = el('tr');
        for (const h of ['ID', 'When', 'Entity', 'Key', 'Action', 'User', ''])
          head.append(el('th', { text: h }));
        const body = el('tbody');
        for (const e of res.value.items) {
          const row = el(
            'tr',
            {},
            el('td', { text: e.audit_id }),
            el('td', { text: e.executed_at }),
            el('td', { text: e.entity }),
            el('td', { text: e.entity_pk }),
            el('td', { text: e.action }),
            testid(el('td', { text: e.user_id ?? 'no known actor' }), `audit-user-${e.audit_id}`),
            el(
              'td',
              {},
              button('View', `view-audit-${e.audit_id}`, () => void open(e.audit_id)),
            ),
          );
          row.dataset.auditId = e.audit_id;
          body.append(row);
        }
        tableArea.replaceChildren(
          testid(el('table', {}, el('thead', {}, head), body), 'audit-table'),
          pager(res.value.page, 'entries', (page) => {
            query = { ...query, page };
            void load();
          }),
        );
      },
    );
  }

  async function open(auditId: string): Promise<void> {
    await renderLatestDetail(
      () => api.getAuditEntry(auditId),
      (res) => {
        if (!res.ok) return void detail.replaceChildren(errorBox(res.error));
        showEntry(res.value);
      },
    );
  }

  function showEntry(e: AuditEntryView): void {
    const facts = el('dl');
    const fact = (name: string, value: string, id: string): void => {
      facts.append(el('dt', { text: name }), testid(el('dd', { text: value }), id));
    };
    fact('Entity', e.entity, 'audit-entity');
    fact('Key', e.entity_pk, 'audit-entity-pk');
    fact('Action', e.action, 'audit-action');
    fact('Acting user', e.user_id ?? 'no known actor', 'audit-user');
    fact('Executed at', e.executed_at, 'audit-executed-at');
    const parts: Node[] = [el('h3', { text: `Audit entry ${e.audit_id}` }), facts];
    const payload = (title: string, text: string | null, id: string): void => {
      parts.push(el('h4', { text: title }));
      const pre = testid(el('pre'), id);
      pre.textContent = text === null ? 'none' : readable(text);
      parts.push(pre);
    };
    payload('Before', e.data_before, 'audit-before');
    payload('After', e.data_after, 'audit-after');
    detail.replaceChildren(...parts);
  }

  host.replaceChildren(el('h2', { text: 'Audit trail' }), filters, tableArea, detail);
  void load();
}
