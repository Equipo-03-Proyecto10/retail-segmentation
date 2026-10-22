import type { ErrorView, Paged, Result } from '../shared/api';

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
  if (error.retry_after !== undefined) row('Retry after', error.retry_after, 'error-retry-after');
  for (const d of error.details) {
    if (!fieldNames.includes(d.field)) row(`Field ${d.field}`, d.code, 'error-detail');
  }
  return box;
}

export type Details = ErrorView['details'];

export function testid<T extends HTMLElement>(node: T, id: string): T {
  node.dataset.testid = id;
  return node;
}

export function button(text: string, id: string, onClick: () => void): HTMLButtonElement {
  const b = testid(el('button', { text }), id);
  b.type = 'button';
  b.addEventListener('click', onClick);
  return b;
}

/** A labelled control with the details of a refusal that name it shown right below it. */
export function formField(
  name: string,
  label: string,
  control: HTMLElement,
  details: Details,
  prefix = 'c',
): HTMLElement {
  control.id = `${prefix}-${name}`;
  control.setAttribute('name', name);
  const lab = el('label', { text: label });
  lab.setAttribute('for', control.id);
  const wrap = el('div', { cls: 'form-field' }, lab, control);
  for (const d of details.filter((x) => x.field === name)) {
    wrap.append(testid(el('p', { cls: 'field-detail', text: `${d.field}: ${d.code}` }), `detail-${name}`));
  }
  return wrap;
}

/** Puts a refusal's details next to the fields of a form that is still on screen, keeping what was typed. */
export function markDetails(form: HTMLFormElement, details: Details): void {
  form.querySelectorAll('.field-detail').forEach((old) => old.remove());
  for (const d of details) {
    const control = form.querySelector(`[name="${CSS.escape(d.field)}"]`);
    control?.parentElement?.append(
      testid(el('p', { cls: 'field-detail', text: `${d.field}: ${d.code}` }), `detail-${d.field}`),
    );
  }
}

export function textInput(value: string, type = 'text'): HTMLInputElement {
  const input = el('input');
  input.type = type;
  input.value = value;
  return input;
}

export function select(options: [string, string][], value: string): HTMLSelectElement {
  const s = el('select');
  for (const [v, text] of options) {
    const o = el('option', { text });
    o.value = v;
    s.append(o);
  }
  s.value = value;
  return s;
}

export function show(
  area: HTMLElement,
  result: Result<unknown>,
  success?: string,
  fields: string[] = [],
): void {
  if (result.ok) {
    area.replaceChildren(
      ...(success === undefined ? [] : [testid(el('p', { cls: 'notice', text: success }), 'notice')]),
    );
  } else {
    area.replaceChildren(errorBox(result.error, fields));
  }
}

export function pager(page: Paged<unknown>['page'], noun: string, go: (page: number) => void): HTMLElement {
  const pages = Math.max(1, Math.ceil(page.total / page.size));
  const summary = testid(
    el('span', { text: `Page ${page.page} of ${pages} · ${page.total} ${noun}` }),
    `${noun}-page`,
  );
  const prev = button('Previous', `${noun}-previous`, () => go(page.page - 1));
  prev.disabled = page.page <= 1;
  const next = button('Next', `${noun}-next`, () => go(page.page + 1));
  next.disabled = page.page >= pages;
  return el('div', { cls: 'pager' }, summary, prev, next);
}
