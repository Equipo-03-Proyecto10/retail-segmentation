import type {
  DuplicateKind,
  ImportFileView,
  ImportRejectionView,
  ImportRunView,
  SaleImportView,
  SaleView,
} from '../shared/api';
import {
  distinct,
  duplicateCounts,
  fieldLabel,
  filterRejections,
  identityText,
  kindLabel,
  KIND_LABEL,
  reconcile,
  type RejectionFilter,
} from '../shared/imports';
import type { ScreenContext } from './catalog';
import { button, el, errorBox, formField, select, testid } from './dom';
import { latestRequest } from './latest-request';

// Import and data quality (F28-02, #871, ADR-0070). The CSV is read by the main process from the
// file the user picks; this screen only shows its name and size, the progress of the import, and
// what the service did with each row. Everything from the service is set with textContent.
//
// The screen holds one table, the rejections: the shared export action writes the first table of
// a screen (ADR-0074), so what is exported is the filtered rejections on screen.

const POLL_MS = 500;
const REPORT_PAGE = 100;
const RECENT = 10;

const PHASE_TEXT: Record<ImportRunView['phase'], string> = {
  creating: 'Opening the import…',
  sending: 'Sending the file in chunks…',
  processing: 'Every chunk is staged. The service is applying the rows…',
  paused: 'Paused. The file is still held, and the import continues where the service says.',
  failed: 'Stopped. The service refused it; cancel the import and start another.',
  finished: 'Finished.',
};

const running = (run: ImportRunView): boolean =>
  run.phase === 'creating' || run.phase === 'sending' || run.phase === 'processing';

const open = (v: SaleImportView): boolean => v.status === 'RECEIVING' || v.status === 'PROCESSING';

function countsView(view: SaleImportView, ids: { counts: string; identity: string }): HTMLElement[] {
  const r = reconcile(view);
  const identity = r.holds
    ? 'The counts add up.'
    : `The counts do not add up: accepted + rejected + pending is ${r.sum}.`;
  const later = r.final ? '' : ' They are final when the import is COMPLETED.';
  return [
    testid(el('p', { text: identityText(r) }), ids.counts),
    testid(el('p', { text: `${identity}${later}` }), ids.identity),
  ];
}

export function renderImports(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const canImport = ctx.can('createSaleImport');

  // ---------- load a file ----------
  const loadMessage = el('div');
  loadMessage.setAttribute('role', 'status');
  const fileInfo = el('div');
  // The live region is the status text only. The buttons live outside it and are rebuilt only when
  // they change, so a poll never takes the keyboard focus from one.
  const progressArea = el('section');
  progressArea.hidden = true;
  const progressText = el('div');
  progressText.setAttribute('role', 'status');
  const progressActions = el('div', { cls: 'actions' });
  progressArea.append(el('h3', { text: 'Progress' }), progressText, progressActions);
  let paintedText = '';
  let paintedActions = '';
  let file: ImportFileView | null = null;
  let run: ImportRunView | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let resumeAfterChoosing: number | undefined;

  const start = button('Start import', 'import-start', () => {
    start.disabled = true;
    loadMessage.replaceChildren();
    void api.startImport().then((r) => {
      if (!r.ok) {
        start.disabled = false;
        loadMessage.replaceChildren(errorBox(r.error));
        if (r.error.code === 'import_in_progress') {
          loadMessage.append(
            testid(
              el('p', {
                cls: 'notice',
                text: 'Another import is open. Resume it with its file, or cancel it, from the recent imports below.',
              }),
              'import-in-progress-hint',
            ),
          );
          void loadRecent();
        }
        return;
      }
      follow(r.value);
    });
  });
  start.disabled = true;

  const choose = button('Choose CSV file…', 'import-choose', () => {
    choose.disabled = true;
    loadMessage.replaceChildren();
    void api.chooseImportFile().then((r) => {
      choose.disabled = false;
      const resumeId = resumeAfterChoosing;
      resumeAfterChoosing = undefined;
      if (!r.ok) {
        file = null;
        start.disabled = true;
        fileInfo.replaceChildren();
        loadMessage.replaceChildren(errorBox(r.error));
        return;
      }
      if (r.value === null) return;
      file = r.value;
      showFile(file);
      if (resumeId === undefined) {
        // A new file replaces the run the main process was holding; so does the panel.
        run = null;
        paintedText = paintedActions = '';
        progressArea.hidden = true;
      }
      if (resumeId !== undefined) {
        void api.resumeImport(resumeId).then((res) => {
          if (!res.ok) loadMessage.replaceChildren(errorBox(res.error));
          else follow(res.value);
        });
      } else {
        start.disabled = !canImport || (run !== null && running(run));
      }
    });
  });
  choose.disabled = !canImport;

  function showFile(f: ImportFileView): void {
    const facts = el('dl');
    const fact = (name: string, value: string, id: string): void => {
      facts.append(el('dt', { text: name }), testid(el('dd', { text: value }), id));
    };
    fact('File', f.name, 'import-file-name');
    fact('Data rows', String(f.total_rows), 'import-file-rows');
    fact('Chunks', String(f.chunk_count), 'import-file-chunks');
    fact('Size', `${f.size_bytes} bytes`, 'import-file-size');
    const parts: Node[] = [facts];
    if (f.blank_lines > 0)
      parts.push(
        testid(
          el('p', { cls: 'notice', text: `${f.blank_lines} empty lines were skipped; they are not rows.` }),
          'import-file-blank',
        ),
      );
    fileInfo.replaceChildren(...parts);
  }

  // ---------- progress ----------
  function paintRun(r: ImportRunView): void {
    progressArea.hidden = false;
    const text = JSON.stringify(r);
    if (text !== paintedText) {
      paintedText = text;
      const parts: Node[] = [testid(el('p', { text: PHASE_TEXT[r.phase] }), 'import-phase')];
      const bar = el('progress');
      bar.max = r.chunk_count;
      bar.value = r.chunks_staged;
      bar.setAttribute('aria-label', 'Chunks staged');
      parts.push(
        testid(bar, 'import-progress'),
        testid(
          el('p', { text: `Chunks staged: ${r.chunks_staged} of ${r.chunk_count} · file ${r.file_name}` }),
          'import-chunks',
        ),
      );
      if (r.import !== undefined) {
        parts.push(
          testid(
            el('p', {
              text: `Rows received: ${r.import.received} of ${r.total_rows} · status ${r.import.status} · import ${r.import.import_id}`,
            }),
            'import-rows',
          ),
          ...countsView(r.import, { counts: 'import-counts', identity: 'import-identity' }),
        );
      }
      if (r.error !== undefined) parts.push(errorBox(r.error));
      progressText.replaceChildren(...parts);
    }
    const importId = r.import?.import_id;
    const resumable = r.can_resume && importId !== undefined;
    const cancellable = r.import !== undefined && open(r.import) && r.phase !== 'creating';
    const actions = `${resumable}|${cancellable}|${importId}`;
    if (actions === paintedActions) return;
    paintedActions = actions;
    const buttons: Node[] = [];
    if (resumable)
      buttons.push(
        button('Resume', 'import-resume', () => {
          void api.resumeImport(importId).then((res) => {
            if (!res.ok) loadMessage.replaceChildren(errorBox(res.error));
            else follow(res.value);
          });
        }),
      );
    if (cancellable)
      buttons.push(
        button('Cancel import', 'import-cancel', () => {
          void api.cancelImport(importId as number).then((res) => {
            if (!res.ok) loadMessage.replaceChildren(errorBox(res.error));
            else void refreshRun();
            void loadRecent();
          });
        }),
      );
    progressActions.replaceChildren(...buttons);
  }

  /** Shows a run and keeps asking the main process for it until it ends. */
  function follow(r: ImportRunView): void {
    run = r;
    start.disabled = true;
    paintRun(r);
    clearTimeout(timer);
    if (running(r)) timer = setTimeout(() => void refreshRun(), POLL_MS);
  }

  async function refreshRun(): Promise<void> {
    const res = await api.importProgress();
    if (!host.isConnected || !res.ok || res.value === null) return;
    const before = run;
    run = res.value;
    paintRun(run);
    clearTimeout(timer);
    if (running(run)) timer = setTimeout(() => void refreshRun(), POLL_MS);
    else if (before !== null && running(before)) {
      // The import has just ended: show what became of it.
      void loadRecent();
      if (run.import !== undefined) void review(run.import.import_id);
    }
  }

  // ---------- recent imports ----------
  const recentArea = el('div');
  const latestRecent = latestRequest();

  async function loadRecent(): Promise<void> {
    await latestRecent(
      () => api.listImports({ page: 1, size: RECENT }),
      (res) => {
        if (!res.ok) return void recentArea.replaceChildren(errorBox(res.error));
        const list = testid(el('ul', { cls: 'imports' }), 'imports-recent');
        for (const v of res.value.items) {
          const item = el(
            'li',
            {},
            el('strong', { text: `Import ${v.import_id}` }),
            el('span', { text: ` · ${v.status} · ${v.total_rows} rows · ${v.created_at}` }),
            el('span', {
              text: ` · received ${v.received}, accepted ${v.accepted}, rejected ${v.rejected}, pending ${v.pending}`,
            }),
            button('Review', `review-${v.import_id}`, () => void review(v.import_id)),
          );
          if (canImport && v.status === 'RECEIVING')
            item.append(
              button('Resume with its file', `resume-recent-${v.import_id}`, () => {
                resumeAfterChoosing = v.import_id;
                choose.click();
              }),
            );
          if (open(v))
            item.append(
              button('Cancel', `cancel-recent-${v.import_id}`, () => {
                void api.cancelImport(v.import_id).then((c) => {
                  if (!c.ok) loadMessage.replaceChildren(errorBox(c.error));
                  void refreshRun();
                  void loadRecent();
                });
              }),
            );
          item.dataset.importId = String(v.import_id);
          list.append(item);
        }
        recentArea.replaceChildren(
          el('h3', { text: 'Recent imports' }),
          res.value.items.length === 0
            ? testid(el('p', { cls: 'notice', text: 'No import has been started yet.' }), 'imports-none')
            : list,
        );
      },
    );
  }

  // ---------- review: counts, rejections, duplicates ----------
  const reviewArea = el('div');
  const rejectionsArea = el('div');
  const reconcileArea = testid(el('section', { cls: 'panel' }), 'reconcile-panel');
  const latestReview = latestRequest();
  let reviewed: SaleImportView | null = null;
  let loaded: ImportRejectionView[] = [];
  let nextPage = 1;
  let total = 0;
  let filter: RejectionFilter = {};
  const filterCode = select([['', 'Any code']], '');
  const filterField = select([['', 'Any field']], '');
  const filterKind = select(
    [
      ['', 'Any kind'],
      ['FILE', KIND_LABEL.FILE],
      ['RECORDED_SALE', KIND_LABEL.RECORDED_SALE],
      ['NONE', 'Not a duplicate'],
    ],
    '',
  );
  const filters = testid(
    el(
      'form',
      { cls: 'filters' },
      formField('code', 'Code', filterCode, [], 'filter'),
      formField('field', 'Field', filterField, [], 'filter'),
      formField('kind', 'Kind', filterKind, [], 'filter'),
    ),
    'rejection-filters',
  );
  filters.addEventListener('submit', (event) => event.preventDefault());
  const onFilter = (): void => {
    filter = {
      ...(filter.firstLine === undefined ? {} : { firstLine: filter.firstLine }),
      ...(filterCode.value ? { code: filterCode.value } : {}),
      ...(filterField.value ? { field: filterField.value } : {}),
      ...(filterKind.value ? { kind: filterKind.value as DuplicateKind | 'NONE' } : {}),
    };
    paintRejections();
  };
  for (const s of [filterCode, filterField, filterKind]) s.addEventListener('change', onFilter);

  function refreshChoices(): void {
    const keep = (s: HTMLSelectElement, any: string, values: string[]): void => {
      const current = s.value;
      s.replaceChildren(
        Object.assign(el('option', { text: any }), { value: '' }),
        ...values.map((v) => Object.assign(el('option', { text: v }), { value: v })),
      );
      s.value = values.includes(current) ? current : '';
    };
    keep(
      filterCode,
      'Any code',
      distinct(loaded, (r) => r.code),
    );
    keep(filterField, 'Any field', distinct(loaded, fieldLabel));
  }

  async function review(importId: number): Promise<void> {
    reviewed = null;
    loaded = [];
    nextPage = 1;
    total = 0;
    filter = {};
    filterCode.value = filterField.value = filterKind.value = '';
    reconcileArea.replaceChildren();
    rejectionsArea.replaceChildren(testid(el('p', { text: 'Loading…' }), 'review-loading'));
    const isCurrent = latestReview.begin();
    const [head, page] = await Promise.all([
      api.getImport(importId),
      api.listImportRejections({ import_id: importId, page: 1, size: REPORT_PAGE }),
    ]);
    if (!isCurrent() || !host.isConnected) return;
    if (!head.ok) {
      reviewArea.replaceChildren(errorBox(head.error));
      rejectionsArea.replaceChildren();
      return;
    }
    reviewed = head.value;
    paintReview(head.value);
    if (!page.ok) return void rejectionsArea.replaceChildren(errorBox(page.error));
    loaded = page.value.items;
    nextPage = 2;
    total = page.value.page.total;
    refreshChoices();
    paintRejections();
  }

  function paintReview(v: SaleImportView): void {
    reviewArea.replaceChildren(
      el('h3', { text: `Import ${v.import_id}` }),
      testid(
        el('p', {
          text: `${v.status} · ${v.total_rows} data rows in ${v.chunk_count} chunks · opened ${v.created_at}`,
        }),
        'review-status',
      ),
      ...countsView(v, { counts: 'review-counts', identity: 'review-identity' }),
      button('Refresh', 'review-refresh', () => void review(v.import_id)),
    );
  }

  function paintRejections(): void {
    const shown = filterRejections(loaded, filter);
    const head = el('tr');
    for (const h of ['Line', 'Field', 'Code', 'Reason', 'Kind', 'First line', 'Sale', ''])
      head.append(el('th', { text: h }));
    const body = el('tbody');
    for (const r of shown) {
      const row = el(
        'tr',
        {},
        el('td', { text: String(r.line) }),
        el('td', { text: fieldLabel(r) }),
        el('td', { text: r.code }),
        el('td', { text: r.message }),
        el('td', { text: kindLabel(r) }),
        el('td', { text: r.first_line === undefined ? '' : String(r.first_line) }),
        el('td', { text: r.sale_id === undefined ? '' : String(r.sale_id) }),
        el('td', {}, ...actionsFor(r)),
      );
      row.dataset.line = String(r.line);
      body.append(row);
    }
    const counts = duplicateCounts(loaded);
    const complete = loaded.length >= total;
    const parts: Node[] = [
      el('h3', { text: 'Rejected rows' }),
      testid(
        el('p', {
          text: `Duplicates: ${counts.inFile} repeats in the file · ${counts.recorded} already recorded${complete ? '' : ' (among those loaded)'}`,
        }),
        'duplicate-summary',
      ),
    ];
    if (filter.firstLine !== undefined) {
      parts.push(
        el(
          'p',
          { cls: 'notice' },
          document.createTextNode(`Showing the lines that repeat line ${filter.firstLine}. `),
          button('Show all', 'clear-repeats', () => {
            // The chip set the kind as well as the line; showing all puts both back.
            filter = { ...filter };
            delete filter.firstLine;
            delete filter.kind;
            filterKind.value = '';
            paintRejections();
          }),
        ),
      );
    }
    parts.push(filters);
    parts.push(
      testid(
        el('p', {
          text: `Showing ${shown.length} of ${loaded.length} loaded rejections · ${total} in the report${
            complete ? '' : '. Filters apply to the rejections loaded so far.'
          }`,
        }),
        'rejections-shown',
      ),
    );
    if (loaded.length === 0 && total === 0)
      parts.push(testid(el('p', { cls: 'notice', text: 'No row was rejected.' }), 'rejections-none'));
    else
      parts.push(
        el(
          'div',
          { cls: 'scroll' },
          testid(el('table', {}, el('thead', {}, head), body), 'rejections-table'),
        ),
      );
    if (!complete) {
      const more = button(`Load the next ${REPORT_PAGE}`, 'rejections-more', () => {
        more.disabled = true;
        void loadPage().then(() => paintRejections());
      });
      const all = button('Load all', 'rejections-all', () => {
        all.disabled = more.disabled = true;
        void loadAll();
      });
      parts.push(el('div', { cls: 'actions' }, more, all));
    }
    rejectionsArea.replaceChildren(...parts);
  }

  async function loadPage(): Promise<boolean> {
    if (reviewed === null) return false;
    const importId = reviewed.import_id;
    const isCurrent = latestReview.begin();
    const res = await api.listImportRejections({ import_id: importId, page: nextPage, size: REPORT_PAGE });
    if (!isCurrent() || !host.isConnected) return false;
    if (!res.ok) {
      rejectionsArea.append(errorBox(res.error));
      return false;
    }
    loaded = [...loaded, ...res.value.items];
    nextPage += 1;
    total = res.value.page.total;
    refreshChoices();
    return res.value.items.length > 0;
  }

  async function loadAll(): Promise<void> {
    while (loaded.length < total) {
      if (!(await loadPage())) break;
      paintRejections();
    }
    paintRejections();
  }

  // ---------- reconciling a duplicate ----------
  function actionsFor(r: ImportRejectionView): Node[] {
    if (r.duplicate_of === 'FILE' && r.first_line !== undefined) {
      const first = r.first_line;
      return [
        button(`Repeats of line ${first}`, `repeats-${r.line}`, () => {
          filter = { ...filter, kind: 'FILE', firstLine: first };
          filterKind.value = 'FILE';
          paintRejections();
          showRepeats(first, r);
        }),
      ];
    }
    if (r.duplicate_of === 'RECORDED_SALE') {
      if (r.sale_id === undefined)
        return [testid(el('span', { text: 'Sale not identified' }), `sale-unknown-${r.line}`)];
      const saleId = r.sale_id;
      return [button(`Open sale ${saleId}`, `open-sale-${r.line}`, () => void openSale(r, saleId))];
    }
    return [];
  }

  function showRepeats(first: number, from: ImportRejectionView): void {
    const lines = loaded
      .filter((x) => x.duplicate_of === 'FILE' && x.first_line === first)
      .map((x) => x.line);
    reconcileArea.replaceChildren(
      el('h3', { text: `Line ${first} and its repeats` }),
      testid(
        el('p', {
          text: `Line ${first} of the file was accepted and recorded once. Lines ${lines.join(', ')} repeat it; nothing was added for them. ${from.message}`,
        }),
        'repeats-text',
      ),
    );
  }

  const latestSale = latestRequest();

  async function openSale(r: ImportRejectionView, saleId: number): Promise<void> {
    reconcileArea.replaceChildren(testid(el('p', { text: 'Loading…' }), 'sale-loading'));
    await latestSale(
      () => api.getSale(saleId),
      (res) => {
        if (!res.ok) return void reconcileArea.replaceChildren(errorBox(res.error));
        reconcileArea.replaceChildren(...saleView(res.value, r));
      },
    );
  }

  function saleView(sale: SaleView, r: ImportRejectionView): Node[] {
    const facts = el('dl');
    const fact = (name: string, value: string, id: string): void => {
      facts.append(el('dt', { text: name }), testid(el('dd', { text: value }), id));
    };
    fact('Sale', String(sale.sale_id), 'sale-id');
    fact('Customer', sale.customer_id, 'sale-customer');
    fact('Store', sale.store_name, 'sale-store');
    fact('Channel', sale.channel_name, 'sale-channel');
    fact('Occurred at', sale.occurred_at, 'sale-occurred-at');
    fact('Total', sale.total, 'sale-total');
    const lines = testid(el('ul'), 'sale-lines');
    for (const l of sale.lines)
      lines.append(
        el('li', { text: `${l.product_name} × ${l.quantity} at ${l.unit_price} = ${l.line_total}` }),
      );
    return [
      el('h3', { text: `Sale ${sale.sale_id}, already recorded` }),
      testid(el('p', { text: `The file's line ${r.line}: ${r.message}` }), 'sale-rejection'),
      facts,
      lines,
    ];
  }

  host.replaceChildren(
    el('h2', { text: 'Import and data quality' }),
    el('h3', { text: 'Load a CSV file' }),
    el('p', {
      text: 'The file is read on this computer, checked, and sent to the sales service in numbered chunks. Rows are accepted or rejected one by one, and a rejected row never stops the others.',
    }),
    el('div', { cls: 'actions' }, choose, start),
    fileInfo,
    loadMessage,
    progressArea,
    recentArea,
    reviewArea,
    rejectionsArea,
    reconcileArea,
  );
  void loadRecent();
  void api.importProgress().then((res) => {
    if (host.isConnected && res.ok && res.value !== null) follow(res.value);
  });
}
