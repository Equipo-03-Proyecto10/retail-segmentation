import type { ExportRequest, MosaiqBridge } from '../shared/api';
import { button, el, errorBox, testid } from './dom';

// The shared export action (F28-01, #870). It reads the table the user is looking at, so it
// exports exactly the filtered, paged rows on screen, and hands it to the main process, which
// checks the role, opens the save dialog and records the export. A role that may not export is
// never given the button.

/** The first table in a screen as header names and cell text; action columns (no header) are left out. */
export function readTable(screen: ParentNode): { columns: string[]; rows: string[][] } | null {
  const table = screen.querySelector('table');
  if (table === null) return null;
  const headers = Array.from(table.querySelectorAll('thead th')).map((h) => h.textContent?.trim() ?? '');
  const keep = headers.map((h, i) => (h === '' ? -1 : i)).filter((i) => i >= 0);
  if (keep.length === 0) return null;
  const rows = Array.from(table.querySelectorAll('tbody tr')).map((tr) => {
    const cells = Array.from(tr.children).map((c) => c.textContent?.trim() ?? '');
    return keep.map((i) => cells[i] ?? '');
  });
  return { columns: keep.map((i) => headers[i] as string), rows };
}

export function exportAction(
  api: Pick<MosaiqBridge, 'exportTable'>,
  module: string,
  screen: ParentNode,
): HTMLElement {
  const result = el('div');
  result.setAttribute('role', 'status');
  const run = button('Export CSV', 'export', () => {
    const table = readTable(screen);
    if (table === null || table.rows.length === 0) {
      result.replaceChildren(
        testid(el('p', { cls: 'notice', text: 'There is nothing to export.' }), 'notice'),
      );
      return;
    }
    const request: ExportRequest = { module, ...table };
    run.disabled = true;
    void api.exportTable(request).then((r) => {
      run.disabled = false;
      if (!r.ok) result.replaceChildren(errorBox(r.error));
      else if (r.value === null) result.replaceChildren();
      else
        result.replaceChildren(
          testid(
            el('p', { cls: 'notice', text: `Exported ${r.value.rows} rows to ${r.value.file}.` }),
            'notice',
          ),
        );
    });
  });
  return el('div', { cls: 'export' }, run, result);
}
