import type {
  FieldDetail,
  ModelFeature,
  ModelMethod,
  ModelVersionView,
  Normalization,
  TrainingDraft,
  TrainingView,
  VersionState,
} from '../shared/api';
import {
  compareVersions,
  FEATURE_UNITS,
  FEATURES,
  labelSizes,
  originText,
  publicationImpact,
  rawCentroids,
  rawText,
  sizeText,
  trainingText,
  type VersionComparison,
} from '../shared/kmeans';
import { SYNTHETIC_LABEL } from '../shared/report-table';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, formField, markDetails, pager, select, show, testid, textInput } from './dom';
import { latestRequest } from './latest-request';

// The K-means configuration module (F28-05, #874). An administrator chooses the variables, the
// normalization, k and the seed and starts a training; the screen follows it until it ends and shows
// the draft version it registered. Any role that reads model versions can open a version, see its
// centroids in raw units and compare two side by side. Publishing a draft says what changes for
// customers first, and keeps the reason. Every figure comes from model-registry: nothing here ranks
// one version above another (RN-39) or edits a version (ADR-0064).

const POLL_MS = 1000;
const NO_VALUE = '—';
const FORM_FIELDS = [
  'version_name',
  'window_days',
  'seed',
  'k',
  'max_iterations',
  'tolerance',
  'features',
  'normalization',
];
const SIZES: [string, string][] = [
  ['20', '20 a page'],
  ['50', '50 a page'],
  ['100', '100 a page'],
];
const NORMALIZATIONS: [string, string][] = [
  ['MIN_MAX', 'Min-max (each variable scaled to 0 to 1)'],
  ['NONE', 'None (raw values)'],
];

/** A refusal may name a field as `parameters.k`; the form names it `k`. */
const onForm = (d: FieldDetail): FieldDetail => ({ ...d, field: d.field.replace(/^parameters\./, '') });

function table(headers: string[], rows: string[][], id: string, numeric: number[] = []): HTMLTableElement {
  const head = el('tr');
  for (const h of headers) {
    const th = el('th', { text: h });
    th.scope = 'col';
    head.append(th);
  }
  const body = el('tbody');
  for (const row of rows) {
    const tr = el('tr');
    row.forEach((cell, i) => tr.append(el('td', { text: cell, cls: numeric.includes(i) ? 'num' : '' })));
    body.append(tr);
  }
  return testid(el('table', {}, el('thead', {}, head), body), id);
}

function facts(entries: [string, string, string][]): HTMLElement {
  const list = el('dl');
  for (const [name, value, id] of entries)
    list.append(el('dt', { text: name }), testid(el('dd', { text: value }), id));
  return list;
}

const orDash = (value: string | undefined): string => value ?? NO_VALUE;
const featureText = (features: ModelFeature[]): string => features.join(', ');

function sizesTable(v: ModelVersionView): HTMLElement {
  return table(
    ['Label', 'Customers (share of those fitted)'],
    labelSizes(v).map((s) => [s.label_code, sizeText(s)]),
    'version-sizes',
    [1],
  );
}

function centroidsTable(v: ModelVersionView): HTMLElement {
  if (v.centroids.length === 0)
    return testid(
      el('p', { cls: 'notice', text: `This ${v.method} version has no centroids.` }),
      'version-no-centroids',
    );
  return table(
    ['Label', `R (${FEATURE_UNITS.R})`, `F (${FEATURE_UNITS.F})`, `M (${FEATURE_UNITS.M})`],
    rawCentroids(v).map((c) => [c.label_code, rawText('R', c.r), rawText('F', c.f), rawText('M', c.m)]),
    'version-centroids',
    [1, 2, 3],
  );
}

function comparisonView(c: VersionComparison): HTMLElement {
  const [a, b] = c.versions;
  const parts: Node[] = [
    testid(
      el('h3', { text: `${a.name} (#${a.version_id}) and ${b.name} (#${b.version_id})` }),
      'compare-title',
    ),
  ];
  if (c.synthetic)
    parts.push(
      testid(
        el('p', {
          cls: 'warning',
          text: `${SYNTHETIC_LABEL}: a version in this comparison was fitted on seeded data (ADR-0052).`,
        }),
        'compare-synthetic',
      ),
    );
  const notes = el('ul');
  for (const n of c.notes) notes.append(el('li', { text: n }));
  parts.push(testid(notes, 'compare-notes'));

  parts.push(
    el('h4', { text: 'Summary' }),
    table(
      ['', a.name, b.name, 'Differs'],
      c.summary.map((r) => [r.name, r.values[0], r.values[1], r.differs ? 'Differs' : '']),
      'compare-summary',
    ),
    el('h4', { text: 'Size under each label' }),
    table(
      ['Label', `${a.name}: customers (share)`, `${b.name}: customers (share)`],
      c.sizes.map((r) => [r.label_code, sizeText(r.cells[0]), sizeText(r.cells[1])]),
      'compare-sizes',
      [1, 2],
    ),
  );

  const group = (name: string): HTMLTableCellElement => {
    const th = el('th', { text: name });
    th.colSpan = 3;
    th.scope = 'colgroup';
    return th;
  };
  const feature = (f: ModelFeature): HTMLTableCellElement => {
    const th = el('th', { text: f });
    th.scope = 'col';
    th.title = FEATURE_UNITS[f];
    return th;
  };
  const labelHead = el('th', { text: 'Label' });
  labelHead.rowSpan = 2;
  labelHead.scope = 'col';
  const body = el('tbody');
  for (const row of c.centroids) {
    const tr = el('tr', {}, el('td', { text: row.label }));
    for (const cell of row.cells)
      for (const f of FEATURES) {
        const raw = cell === null ? null : cell[f === 'R' ? 'r' : f === 'F' ? 'f' : 'm'];
        tr.append(el('td', { text: rawText(f, raw), cls: 'num' }));
      }
    body.append(tr);
  }
  parts.push(
    el('h4', { text: 'Centroids in raw units' }),
    el('p', {
      text: `R is ${FEATURE_UNITS.R}, F is ${FEATURE_UNITS.F} and M is ${FEATURE_UNITS.M}.`,
    }),
    el(
      'div',
      { cls: 'scroll' },
      testid(
        el(
          'table',
          {},
          el(
            'thead',
            {},
            el('tr', {}, labelHead, group(a.name), group(b.name)),
            el('tr', {}, ...FEATURES.map(feature), ...FEATURES.map(feature)),
          ),
          body,
        ),
        'compare-centroids',
      ),
    ),
  );
  return el('section', { cls: 'panel' }, ...parts);
}

export function renderKmeans(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const message = testid(el('div'), 'kmeans-message');
  message.setAttribute('role', 'status');
  const progress = testid(el('section', { cls: 'panel' }), 'training-progress');
  progress.setAttribute('aria-live', 'polite');
  const listArea = el('div');
  const comparison = testid(el('div'), 'comparison');
  const detail = testid(el('section', { cls: 'panel' }), 'version-detail');
  const dialog = testid(el('dialog'), 'publish-dialog');
  const renderLatestList = latestRequest();
  const renderLatestDetail = latestRequest();
  const renderLatestCompare = latestRequest();
  let query: { method?: ModelMethod; state?: VersionState; page: number; size: number } = {
    method: 'KMEANS',
    page: 1,
    size: 20,
  };
  /** The versions ticked for comparison, oldest tick first; only the last two stay ticked. */
  let picked: number[] = [];
  let following = 0;
  /** Counts confirmations opened, so a slow answer cannot fill the dialog of a later one. */
  let dialogFor = 0;

  // ---------- following a training ----------

  function trainingView(t: TrainingView): HTMLElement[] {
    const parts: HTMLElement[] = [
      testid(el('h3', { text: `Training #${t.training_id}: ${t.version_name}` }), 'training-title'),
    ];
    if (t.state === 'RUNNING') {
      const bar = el('progress');
      bar.setAttribute('aria-label', 'Training progress');
      parts.push(bar);
    }
    parts.push(
      testid(el('p', { text: trainingText(t, Date.now()) }), 'training-text'),
      testid(el('p', { cls: 'training-state', text: t.state }), 'training-state'),
    );
    const entries: [string, string, string][] = [
      ['Seed', t.seed, 'training-seed'],
      ['Clusters (k)', t.parameters.k === undefined ? NO_VALUE : String(t.parameters.k), 'training-k'],
      ['Variables', featureText(t.parameters.features), 'training-features'],
      ['Normalization', t.parameters.normalization, 'training-normalization'],
      ['Window (days)', String(t.window_days), 'training-window'],
    ];
    if (t.state === 'COMPLETED' && t.metrics !== undefined)
      entries.push(
        ['Inertia', orDash(t.metrics.inertia), 'training-inertia'],
        ['Silhouette', orDash(t.metrics.silhouette), 'training-silhouette'],
      );
    if (t.dataset !== undefined)
      entries.push([
        'Data origin',
        t.dataset.origin === 'SEEDED' ? `${SYNTHETIC_LABEL} (seeded)` : 'Observed',
        'training-origin',
      ]);
    parts.push(facts(entries));
    if (t.state === 'COMPLETED' && t.version_id !== undefined) {
      const versionId = t.version_id;
      parts.push(
        testid(
          el('p', {
            cls: 'notice',
            text: `The result is stored as the draft version "${t.version_name}" (#${versionId}). Nothing is published.`,
          }),
          'training-stored',
        ),
        button(`Open draft version #${versionId}`, 'training-open-version', () => void open(versionId)),
      );
    }
    return parts;
  }

  function follow(first: TrainingView): void {
    const token = ++following;
    const render = (t: TrainingView): void => progress.replaceChildren(...trainingView(t));
    const poll = (t: TrainingView): void => {
      setTimeout(() => {
        if (token !== following || !progress.isConnected) return;
        void api.getTraining(t.training_id).then((res) => {
          if (token !== following || !progress.isConnected) return;
          if (!res.ok) {
            progress.replaceChildren(
              errorBox(res.error),
              button('Check again', 'training-recheck', () => poll(t)),
            );
            return;
          }
          settle(res.value);
        });
      }, POLL_MS);
    };
    const settle = (t: TrainingView): void => {
      render(t);
      if (t.state === 'RUNNING') return poll(t);
      // The run is over: the draft it registered is a version, so the list and the detail show it.
      void load();
      if (t.state === 'COMPLETED' && t.version_id !== undefined) void open(t.version_id);
    };
    settle(first);
  }

  /** A training that is already running (started here earlier, or by another administrator) is followed too. */
  async function resume(): Promise<void> {
    const res = await api.listTrainings({ state: 'RUNNING', page: 1, size: 1 });
    const running = res.ok ? res.value.items[0] : undefined;
    if (running !== undefined) follow(running);
  }

  // ---------- the training form ----------

  function trainingForm(): HTMLElement {
    const name = textInput('');
    const windowDays = textInput('180', 'number');
    const k = textInput('4', 'number');
    const iterations = textInput('100', 'number');
    const tolerance = textInput('0.0001');
    const seed = textInput('');
    const normalization = select(NORMALIZATIONS, 'MIN_MAX');
    const boxes = new Map<ModelFeature, HTMLInputElement>();
    const variables = el('fieldset', {}, el('legend', { text: 'Variables' }));
    variables.setAttribute('name', 'features');
    for (const f of FEATURES) {
      const box = el('input');
      box.type = 'checkbox';
      box.value = f;
      box.checked = true;
      box.dataset.testid = `feature-${f}`;
      boxes.set(f, box);
      variables.append(el('label', {}, box, document.createTextNode(` ${f}: ${FEATURE_UNITS[f]}`)));
    }
    const start = el('button', { text: 'Start the training' });
    start.type = 'submit';
    const form = testid(
      el(
        'form',
        {},
        el('h3', { text: 'Train a K-means model' }),
        formField('version_name', 'Name of the draft version', name, [], 'train'),
        el('div', { cls: 'form-field' }, variables),
        formField('normalization', 'Normalization', normalization, [], 'train'),
        formField('k', 'Clusters (k, 2 to 20)', k, [], 'train'),
        formField('seed', 'Seed (leave empty and the service chooses and records one)', seed, [], 'train'),
        formField('window_days', 'Window in days (1 to 3650)', windowDays, [], 'train'),
        formField('max_iterations', 'Iteration limit (1 to 1000)', iterations, [], 'train'),
        formField('tolerance', 'Tolerance (greater than 0)', tolerance, [], 'train'),
        start,
      ),
      'training-form',
    );
    form.noValidate = true;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const draft: TrainingDraft = {
        version_name: name.value,
        window_days: Number(windowDays.value),
        k: Number(k.value),
        max_iterations: Number(iterations.value),
        tolerance: tolerance.value,
        features: FEATURES.filter((f) => boxes.get(f)?.checked === true),
        normalization: normalization.value as Normalization,
        ...(seed.value.trim() === '' ? {} : { seed: seed.value }),
      };
      start.disabled = true;
      void api.startTraining(draft).then((res) => {
        start.disabled = false;
        if (!res.ok) {
          show(message, res, '', [...FORM_FIELDS, ...FORM_FIELDS.map((f) => `parameters.${f}`)]);
          markDetails(form, res.error.details.map(onForm));
          // Two trainings never run at once: show the one that is running.
          if (res.error.code === 'training_in_progress') void resume();
          return;
        }
        message.replaceChildren(
          testid(
            el('p', {
              cls: 'notice',
              text: `Training #${res.value.training_id} started. Its result will be stored as the draft version "${res.value.version_name}".`,
            }),
            'notice',
          ),
        );
        form.querySelectorAll('.field-detail').forEach((old) => old.remove());
        follow(res.value);
      });
    });
    return form;
  }

  // ---------- the versions ----------

  const methodFilter = select(
    [
      ['KMEANS', 'K-means'],
      ['', 'Any method'],
    ],
    'KMEANS',
  );
  const stateFilter = select(
    [
      ['', 'Any state'],
      ['DRAFT', 'DRAFT'],
      ['PUBLISHED', 'PUBLISHED'],
      ['RETIRED', 'RETIRED'],
    ],
    '',
  );
  const sizeFilter = select(SIZES, '20');
  const search = el('button', { text: 'Search' });
  search.type = 'submit';
  const filters = testid(
    el(
      'form',
      { cls: 'filters' },
      formField('method', 'Method', methodFilter, [], 'filter'),
      formField('state', 'State', stateFilter, [], 'filter'),
      formField('size', 'Page size', sizeFilter, [], 'filter'),
      search,
    ),
    'version-filters',
  );
  filters.noValidate = true;
  filters.addEventListener('submit', (event) => {
    event.preventDefault();
    query = { page: 1, size: Number(sizeFilter.value) };
    if (methodFilter.value !== '') query.method = methodFilter.value as ModelMethod;
    if (stateFilter.value !== '') query.state = stateFilter.value as VersionState;
    void load();
  });

  const compareButton = button(
    'Compare the two ticked versions',
    'compare-selected',
    () => void compareTicked(),
  );

  function tick(versionId: number, on: boolean): void {
    picked = picked.filter((p) => p !== versionId);
    if (on) picked = [...picked, versionId].slice(-2);
    listArea.querySelectorAll<HTMLInputElement>('input[data-version]').forEach((box) => {
      box.checked = picked.includes(Number(box.dataset.version));
    });
    compareButton.disabled = picked.length !== 2;
  }

  async function load(): Promise<void> {
    await renderLatestList(
      () => api.listModelVersions(query),
      (res) => {
        if (!res.ok) return void listArea.replaceChildren(errorBox(res.error));
        const head = el('tr');
        for (const h of [
          '',
          'ID',
          'Name',
          'State',
          'Method',
          'k',
          'Variables',
          'Normalization',
          'Seed',
          'Inertia',
          'Silhouette',
          'Data',
          'Created',
          '',
        ]) {
          const th = el('th', { text: h });
          th.scope = 'col';
          head.append(th);
        }
        const body = el('tbody');
        for (const v of res.value.items) {
          const box = el('input');
          box.type = 'checkbox';
          box.dataset.version = String(v.version_id);
          box.dataset.testid = `tick-${v.version_id}`;
          box.checked = picked.includes(v.version_id);
          box.setAttribute('aria-label', `Compare ${v.name}`);
          box.addEventListener('change', () => tick(v.version_id, box.checked));
          const row = el(
            'tr',
            {},
            el('td', {}, box),
            el('td', { text: String(v.version_id) }),
            el('td', { text: v.name }),
            testid(el('td', { text: v.state }), `version-state-${v.version_id}`),
            el('td', { text: v.method }),
            el('td', { text: v.parameters.k === undefined ? NO_VALUE : String(v.parameters.k) }),
            el('td', { text: featureText(v.parameters.features) }),
            el('td', { text: v.parameters.normalization }),
            el('td', { text: orDash(v.seed) }),
            el('td', { text: orDash(v.metrics.inertia), cls: 'num' }),
            el('td', { text: orDash(v.metrics.silhouette), cls: 'num' }),
            el('td', { text: originText(v) }),
            el('td', { text: v.created_at }),
            el(
              'td',
              {},
              button('View', `view-version-${v.version_id}`, () => void open(v.version_id)),
            ),
          );
          row.dataset.versionId = String(v.version_id);
          body.append(row);
        }
        listArea.replaceChildren(
          testid(el('table', {}, el('thead', {}, head), body), 'versions-table'),
          pager(res.value.page, 'versions', (page) => {
            query = { ...query, page };
            void load();
          }),
        );
        compareButton.disabled = picked.length !== 2;
      },
    );
  }

  async function open(versionId: number): Promise<void> {
    await renderLatestDetail(
      () => api.getModelVersion(versionId),
      (res) => {
        if (!res.ok) return void detail.replaceChildren(errorBox(res.error));
        showVersion(res.value);
      },
    );
  }

  function showVersion(v: ModelVersionView): void {
    const entries: [string, string, string][] = [
      ['State', v.state, 'version-state'],
      ['Source', v.source, 'version-source'],
      ['Method', v.method, 'version-method'],
      ['Clusters (k)', v.parameters.k === undefined ? NO_VALUE : String(v.parameters.k), 'version-k'],
      ['Variables', featureText(v.parameters.features), 'version-features'],
      ['Normalization', v.parameters.normalization, 'version-normalization'],
      ['Seed', orDash(v.seed), 'version-seed'],
      ['Window (days)', String(v.window_days), 'version-window'],
      [
        'Iteration limit',
        v.parameters.max_iterations === undefined ? NO_VALUE : String(v.parameters.max_iterations),
        'version-iterations',
      ],
      ['Tolerance', orDash(v.parameters.tolerance), 'version-tolerance'],
      ['Data origin', originText(v), 'version-origin'],
      ['Customers fitted on', String(v.sample_size), 'version-sample'],
      ['Inertia', orDash(v.metrics.inertia), 'version-inertia'],
      ['Silhouette', orDash(v.metrics.silhouette), 'version-silhouette'],
      ['Stability', orDash(v.metrics.stability), 'version-stability'],
      ['Created', v.created_at, 'version-created'],
      ['Published', orDash(v.published_at), 'version-published'],
      ['Retired', orDash(v.retired_at), 'version-retired'],
    ];
    const parts: Node[] = [el('h3', { text: `${v.name} (#${v.version_id})` }), facts(entries)];
    if (v.dataset.origin === 'SEEDED')
      parts.push(
        el('p', {
          cls: 'warning',
          text: `${SYNTHETIC_LABEL}: this version was fitted on seeded data (ADR-0052).`,
        }),
      );
    parts.push(
      el('h4', { text: 'Centroids in raw units' }),
      centroidsTable(v),
      el('h4', { text: 'Size under each label' }),
      sizesTable(v),
    );
    const actions = el('div', { cls: 'actions' });
    actions.append(
      button('Compare with the published version', 'compare-published', () => void compareWithPublished(v)),
    );
    // Only a role that may publish is given the control; the service still refuses anyone else.
    if (v.state === 'DRAFT' && ctx.can('publishModelVersion'))
      actions.append(button('Publish this draft…', 'publish-open', () => void confirmPublish(v)));
    parts.push(actions);
    detail.replaceChildren(...parts);
  }

  // ---------- comparing ----------

  function showComparison(a: ModelVersionView, b: ModelVersionView): void {
    comparison.replaceChildren(comparisonView(compareVersions(a, b)));
  }

  async function compareTicked(): Promise<void> {
    if (picked.length !== 2) return;
    const [first, second] = picked as [number, number];
    await renderLatestCompare(
      () => Promise.all([api.getModelVersion(first), api.getModelVersion(second)]),
      ([a, b]) => {
        if (!a.ok) return void comparison.replaceChildren(errorBox(a.error));
        if (!b.ok) return void comparison.replaceChildren(errorBox(b.error));
        showComparison(a.value, b.value);
      },
    );
  }

  async function compareWithPublished(v: ModelVersionView): Promise<void> {
    await renderLatestCompare(
      () => api.listModelVersions({ state: 'PUBLISHED', page: 1, size: 1 }),
      (res) => {
        if (!res.ok) return void comparison.replaceChildren(errorBox(res.error));
        const published = res.value.items[0];
        if (published === undefined)
          return void comparison.replaceChildren(
            testid(
              el('p', {
                cls: 'notice',
                text: 'No version is published yet, so there is nothing to compare with.',
              }),
              'compare-none',
            ),
          );
        if (published.version_id === v.version_id)
          return void comparison.replaceChildren(
            testid(el('p', { cls: 'notice', text: 'This is the published version.' }), 'compare-none'),
          );
        showComparison(v, published);
      },
    );
  }

  // ---------- publishing ----------

  /** The confirmation first states what changes for customers, from the version published right now. */
  async function confirmPublish(v: ModelVersionView): Promise<void> {
    const mine = ++dialogFor;
    const close = button('Close', 'publish-close', () => dialog.close());
    dialog.replaceChildren(
      el('h3', { text: `Publish "${v.name}"` }),
      testid(el('p', { text: 'Reading the version that is published now…' }), 'publish-loading'),
      close,
    );
    if (!dialog.open) dialog.showModal();
    const res = await api.listModelVersions({ state: 'PUBLISHED', page: 1, size: 1 });
    if (mine !== dialogFor) return;
    if (!res.ok) {
      // Without the published version the statement would be incomplete, so nothing can be confirmed.
      dialog.replaceChildren(el('h3', { text: `Publish "${v.name}"` }), errorBox(res.error), close);
      return;
    }
    const impact = el('ul');
    for (const line of publicationImpact(v, res.value.items[0] ?? null))
      impact.append(el('li', { text: line }));
    const reason = el('textarea');
    reason.maxLength = 500;
    const form = el('form');
    form.noValidate = true;
    const confirm = el('button', { text: 'Confirm publication' });
    confirm.type = 'submit';
    confirm.dataset.testid = 'confirm-publish';
    form.append(
      formField(
        'reason',
        'Reason (kept in the publication record, up to 500 characters)',
        reason,
        [],
        'publish',
      ),
      confirm,
      button('Cancel', 'cancel-publish', () => dialog.close()),
    );
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (reason.value.trim() === '') return markDetails(form, [{ field: 'reason', code: 'required' }]);
      confirm.disabled = true;
      void api.publishModelVersion(v.version_id, reason.value).then(async (out) => {
        confirm.disabled = false;
        if (!out.ok && out.error.details.length > 0 && out.error.code === 'invalid_body')
          return markDetails(form, out.error.details);
        dialog.close();
        show(
          message,
          out,
          out.ok
            ? `"${v.name}" (#${v.version_id}) is published (publication #${out.value.publication_id}). Customers are scored under it in a full run that follows.`
            : '',
        );
        // A refused publication leaves the screen as the service has it: read the version and the list again.
        await Promise.all([open(v.version_id), load()]);
      });
    });
    dialog.replaceChildren(
      el('h3', { text: `Publish "${v.name}"` }),
      el('h4', { text: 'What changes for customers' }),
      testid(el('div', { cls: 'warning' }, impact), 'publish-impact'),
      form,
    );
  }

  const parts: Node[] = [el('h2', { text: 'K-means models' })];
  if (ctx.can('createModelTraining')) parts.push(trainingForm());
  else
    parts.push(
      testid(
        el('p', {
          cls: 'notice',
          text: 'Your role reads and compares model versions. Starting a training and publishing a version are not open to it.',
        }),
        'read-only-note',
      ),
    );
  parts.push(
    message,
    progress,
    el('h3', { text: 'Versions' }),
    filters,
    listArea,
    compareButton,
    comparison,
    detail,
    dialog,
  );
  host.replaceChildren(...parts);
  compareButton.disabled = true;
  void load();
  void resume();
}
