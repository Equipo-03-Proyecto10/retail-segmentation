import type { AuditEntryView, AuditQuery, DomainEventQuery, DomainEventView } from '../shared/api';
import {
  AUDIT_ACTIONS,
  AUDITED_ENTITIES,
  DOMAIN_EVENT_KINDS,
  DOMAIN_EVENT_OUTCOMES,
  DOMAIN_EVENT_SUBJECT_TYPES,
} from '../shared/api';
import { AUDIT_TOPICS, type AuditTopic, type AuditTopicPreset } from '../shared/audit-topics';
import { AUDIT_OPERATIONS } from '../shared/modules';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, formField, pager, select, testid, textInput } from './dom';
import { latestRequest } from './latest-request';

// The audit trail (F16-09, #487). Read only: the trail is append-only, so the
// screen draws no create, edit or delete control. The acting user is shown by
// its user_id. Payloads are JSON text; they are formatted and set with
// textContent, never as HTML.
//
// F28-15 (#884) adds the decisions the trail records besides a row change (ADR-0072): model
// versions, calculations, permissions, critical acts and reads of the trail, each with its actor,
// its subject and the version it was computed under. The screen has two lists, row changes and
// decisions, and four shortcuts that fill in their filters. Nothing the service sends is set as
// HTML: every value is text.

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

function renderRows(host: HTMLElement, ctx: ScreenContext, preset: AuditTopicPreset): void {
  const { api } = ctx;
  const tableArea = el('div');
  const detail = testid(el('section', { cls: 'panel' }), 'audit-detail');
  const renderLatestList = latestRequest();
  const renderLatestDetail = latestRequest();
  let query: AuditQuery = { page: 1, size: 20, ...(preset.entity ? { entity: preset.entity } : {}) };

  const entity = select(
    [['', 'Any entity'], ...AUDITED_ENTITIES.map((e): [string, string] => [e, e])],
    preset.entity ?? '',
  );
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
          el('div', { cls: 'scroll' }, testid(el('table', {}, el('thead', {}, head), body), 'audit-table')),
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

  host.replaceChildren(filters, tableArea, detail);
  void load();
}

/** Who acted, as the trail names them: the role and the id, or the system for a schedule. */
function actorOf(e: DomainEventView): string {
  return e.actor_user_id === null ? 'system' : `${e.actor_role ?? 'unknown role'} ${e.actor_user_id}`;
}

/** The versions an event names: the model version it concerns or was scored under, and its rule set. */
function versionOf(e: DomainEventView): string {
  const parts: string[] = [];
  if (e.model_version_id !== null) parts.push(`model ${e.model_version_id}`);
  if (e.rule_set_version !== null) parts.push(`rule set ${e.rule_set_version}`);
  return parts.length === 0 ? 'none' : parts.join(', ');
}

/** What the event is about; a read of a list of the trail names nothing. */
function subjectOf(e: DomainEventView): string {
  if (e.subject_type === null) return 'none';
  return e.subject_id === null ? e.subject_type : `${e.subject_type} ${e.subject_id}`;
}

function renderEvents(host: HTMLElement, ctx: ScreenContext, preset: AuditTopicPreset): void {
  const { api } = ctx;
  const tableArea = el('div');
  const detail = testid(el('section', { cls: 'panel' }), 'event-detail');
  const renderLatestList = latestRequest();
  const renderLatestDetail = latestRequest();
  let query: DomainEventQuery = { page: 1, size: 20 };
  if (preset.kind) query.kind = preset.kind;
  if (preset.subject_type) query.subject_type = preset.subject_type;

  const kind = select(
    [['', 'Any kind'], ...DOMAIN_EVENT_KINDS.map((k): [string, string] => [k, k])],
    preset.kind ?? '',
  );
  const actor = textInput('');
  const role = textInput('');
  role.placeholder = 'AUDITOR';
  const version = textInput('');
  const subjectType = select(
    [['', 'Any subject'], ...DOMAIN_EVENT_SUBJECT_TYPES.map((t): [string, string] => [t, t])],
    preset.subject_type ?? '',
  );
  const subjectId = textInput('');
  const outcome = select(
    [['', 'Any outcome'], ...DOMAIN_EVENT_OUTCOMES.map((o): [string, string] => [o, o])],
    '',
  );
  const from = textInput('');
  const to = textInput('');
  from.placeholder = to.placeholder = '2026-11-01T00:00:00Z';
  const size = select(SIZES, '20');
  const search = el('button', { text: 'Search' });
  search.type = 'submit';
  const filters = testid(
    el(
      'form',
      { cls: 'filters' },
      formField('kind', 'Kind', kind, [], 'filter'),
      formField('actor_user_id', 'Actor (user id)', actor, [], 'filter'),
      formField('actor_role', 'Actor role', role, [], 'filter'),
      formField('model_version_id', 'Model version', version, [], 'filter'),
      formField('subject_type', 'Subject', subjectType, [], 'filter'),
      formField('subject_id', 'Subject id (with a subject)', subjectId, [], 'filter'),
      formField('outcome', 'Outcome', outcome, [], 'filter'),
      formField('from', 'Recorded from (RFC 3339)', from, [], 'filter'),
      formField('to', 'Recorded to (RFC 3339)', to, [], 'filter'),
      formField('size', 'Page size', size, [], 'filter'),
      search,
    ),
    'event-filters',
  );
  filters.noValidate = true;
  filters.addEventListener('submit', (event) => {
    event.preventDefault();
    query = { page: 1, size: Number(size.value) };
    if (kind.value) query.kind = kind.value;
    if (actor.value.trim()) query.actor_user_id = actor.value.trim();
    if (role.value.trim()) query.actor_role = role.value.trim().toUpperCase();
    if (version.value.trim()) query.model_version_id = version.value.trim();
    if (subjectType.value) query.subject_type = subjectType.value;
    if (subjectId.value.trim()) query.subject_id = subjectId.value.trim();
    if (outcome.value) query.outcome = outcome.value;
    if (from.value.trim()) query.from = from.value.trim();
    if (to.value.trim()) query.to = to.value.trim();
    void load();
  });

  async function load(): Promise<void> {
    await renderLatestList(
      () => api.listDomainEvents(query),
      (res) => {
        // A refusal (invalid_query, forbidden_role, a contract defect) is shown as returned.
        if (!res.ok) return void tableArea.replaceChildren(errorBox(res.error));
        const head = el('tr');
        for (const h of ['Recorded', 'Kind', 'Outcome', 'Actor', 'Subject', 'Version', ''])
          head.append(el('th', { text: h }));
        const body = el('tbody');
        for (const e of res.value.items) {
          const row = el(
            'tr',
            {},
            el('td', { text: e.recorded_at }),
            el('td', { text: e.kind }),
            el('td', { text: e.outcome }),
            testid(el('td', { text: actorOf(e) }), `event-actor-${e.event_id}`),
            el('td', { text: subjectOf(e) }),
            testid(el('td', { text: versionOf(e) }), `event-version-${e.event_id}`),
            el(
              'td',
              {},
              button('View', `view-event-${e.event_id}`, () => void open(e.event_id)),
            ),
          );
          row.dataset.eventId = e.event_id;
          body.append(row);
        }
        tableArea.replaceChildren(
          el('div', { cls: 'scroll' }, testid(el('table', {}, el('thead', {}, head), body), 'events-table')),
          pager(res.value.page, 'events', (page) => {
            query = { ...query, page };
            void load();
          }),
        );
      },
    );
  }

  async function open(eventId: string): Promise<void> {
    await renderLatestDetail(
      () => api.getDomainEvent(eventId),
      (res) => {
        if (!res.ok) return void detail.replaceChildren(errorBox(res.error));
        showEvent(res.value);
      },
    );
  }

  function showEvent(e: DomainEventView): void {
    const facts = el('dl');
    const fact = (name: string, value: string, id: string): void => {
      facts.append(el('dt', { text: name }), testid(el('dd', { text: value }), id));
    };
    fact('Kind', e.kind, 'event-kind');
    fact('Outcome', e.outcome, 'event-outcome');
    fact('Recorded at', e.recorded_at, 'event-recorded-at');
    fact('Occurred at', e.occurred_at, 'event-occurred-at');
    fact('Actor', actorOf(e), 'event-actor');
    fact('Source', e.source, 'event-source');
    fact('Store', e.store_id === null ? 'none' : String(e.store_id), 'event-store');
    fact('Subject', subjectOf(e), 'event-subject');
    fact('Version', versionOf(e), 'event-version');
    fact('Correlation id', e.correlation_id ?? 'none', 'event-correlation');
    const keys = el('dl');
    for (const d of e.details)
      keys.append(el('dt', { text: d.key }), testid(el('dd', { text: d.value }), `event-detail-${d.key}`));
    const chain = el('dl');
    chain.append(
      el('dt', { text: 'Previous hash' }),
      testid(el('dd', { text: e.prev_hash }), 'event-prev-hash'),
      el('dt', { text: 'Entry hash' }),
      testid(el('dd', { text: e.entry_hash }), 'event-entry-hash'),
    );
    detail.replaceChildren(
      el('h3', { text: `Decision ${e.event_id}` }),
      facts,
      el('h4', { text: 'Details' }),
      e.details.length === 0 ? testid(el('p', { text: 'none' }), 'event-no-details') : keys,
      el('h4', { text: 'Chain' }),
      chain,
    );
  }

  host.replaceChildren(
    el('p', {
      cls: 'hint',
      text: 'Decisions the trail records besides row changes: model versions, calculations, permissions, critical acts and reads of the trail. They carry ids, codes, versions and counts, never the data read.',
    }),
    filters,
    tableArea,
    detail,
  );
  void load();
}

type Source = 'rows' | 'events';

export function renderAudit(host: HTMLElement, ctx: ScreenContext): void {
  const events = ctx.can(AUDIT_OPERATIONS.events);
  const topics = AUDIT_TOPICS.filter((t) => ctx.can(t.operation));
  const panel = el('div');
  let source: Source = 'rows';
  let preset: AuditTopicPreset = {};
  let topic: AuditTopic['id'] | null = null;

  const tabs = testid(el('div', { cls: 'audit-tabs' }), 'audit-tabs');
  const shortcuts = testid(el('div', { cls: 'audit-tabs' }), 'audit-topics');
  const tabButtons = new Map<Source, HTMLButtonElement>();
  const topicButtons = new Map<AuditTopic['id'], HTMLButtonElement>();

  function draw(): void {
    for (const [name, b] of tabButtons)
      b.setAttribute('aria-current', String(topic === null && name === source));
    for (const [id, b] of topicButtons) b.setAttribute('aria-current', String(id === topic));
    if (source === 'events') renderEvents(panel, ctx, preset);
    else renderRows(panel, ctx, preset);
  }
  function choose(next: Source, nextPreset: AuditTopicPreset, nextTopic: AuditTopic['id'] | null): void {
    source = next;
    preset = nextPreset;
    topic = nextTopic;
    draw();
  }

  const rowsTab = button('Row changes', 'audit-tab-rows', () => choose('rows', {}, null));
  tabButtons.set('rows', rowsTab);
  tabs.append(rowsTab);
  if (events) {
    const eventsTab = button('Decisions and versions', 'audit-tab-events', () => choose('events', {}, null));
    tabButtons.set('events', eventsTab);
    tabs.append(eventsTab);
  }
  for (const t of topics) {
    const b = button(t.label, `audit-topic-${t.id}`, () => choose(t.source, t.preset, t.id));
    topicButtons.set(t.id, b);
    shortcuts.append(b);
  }

  host.replaceChildren(el('h2', { text: 'Audit trail' }), tabs, shortcuts, panel);
  draw();
}
