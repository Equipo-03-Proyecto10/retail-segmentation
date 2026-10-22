import type { ErrorView } from '../shared/api';

// DOM helpers shared by every screen. Text is always set with textContent, never as HTML.

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: { text?: string; cls?: string } = {},
  ...children: Node[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (props.text !== undefined) node.textContent = props.text;
  if (props.cls !== undefined) node.className = props.cls;
  node.append(...children);
  return node;
}

/** Shows the envelope: code, message and correlation id; details go next to their fields. */
export function errorBox(error: ErrorView, fieldNames: string[] = []): HTMLElement {
  const box = el('dl', { cls: 'error' });
  box.setAttribute('role', 'alert');
  box.dataset.testid = 'error';
  const row = (name: string, value: string, id: string): void => {
    const dd = el('dd', { text: value });
    dd.dataset.testid = id;
    box.append(el('dt', { text: name }), dd);
  };
  row('Code', error.code, 'error-code');
  row('Message', error.message, 'error-message');
  row('Correlation id', error.correlation_id, 'error-correlation');
  for (const d of error.details) {
    if (!fieldNames.includes(d.field)) row(`Field ${d.field}`, d.code, 'error-detail');
  }
  return box;
}
