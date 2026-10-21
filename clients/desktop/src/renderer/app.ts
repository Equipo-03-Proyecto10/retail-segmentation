import type { AppState, ErrorView, MosaiqBridge } from '../shared/api';

declare global {
  interface Window {
    mosaiq: MosaiqBridge;
  }
}

const root = document.getElementById('app') as HTMLElement;
const api = window.mosaiq;

function el<K extends keyof HTMLElementTagNameMap>(
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
function errorBox(error: ErrorView, fieldNames: string[] = []): HTMLElement {
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

function field(
  name: string,
  label: string,
  type: string,
  details: ErrorView['details'],
): { wrap: HTMLElement; input: HTMLInputElement } {
  const input = el('input');
  input.name = name;
  input.type = type;
  input.id = `f-${name}`;
  input.required = true;
  input.autocomplete = type === 'password' ? 'current-password' : 'username';
  const wrap = el('div', {}, el('label', { text: label }), input);
  wrap.querySelector('label')?.setAttribute('for', input.id);
  for (const d of details.filter((x) => x.field === name)) {
    const p = el('p', { cls: 'field-detail', text: `${d.field}: ${d.code}` });
    p.dataset.testid = `detail-${name}`;
    wrap.append(p);
  }
  return { wrap, input };
}

function renderSignIn(state: Extract<AppState, { status: 'signed_out' }>, error?: ErrorView): void {
  const details = error?.details ?? [];
  const email = field('email', 'Email', 'email', details);
  const password = field('password', 'Password', 'password', details);
  const submit = el('button', { text: 'Sign in' });
  submit.type = 'submit';
  const form = el('form', {}, email.wrap, password.wrap, submit);
  form.noValidate = true;
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    submit.disabled = true;
    void api.signIn({ email: email.input.value, password: password.input.value }).then((result) => {
      if (result.ok) return render(result.state);
      render(result.state, result.error);
    });
  });
  const parts: Node[] = [el('h1', { text: 'MOSAIQ back office' })];
  if (state.notice !== undefined) {
    const notice = el('p', { cls: 'notice', text: state.notice });
    notice.dataset.testid = 'notice';
    parts.push(notice);
  }
  if (error) parts.push(errorBox(error, ['email', 'password']));
  parts.push(form);
  root.replaceChildren(...parts);
}

function renderHome(state: Extract<AppState, { status: 'signed_in' }>): void {
  const result = el('div');
  const list = el('ul', { cls: 'services' });
  for (const s of state.services) {
    const probe = el('button', { text: 'Check' });
    probe.addEventListener('click', () => {
      void api.probeService(s.name).then((r) => {
        result.replaceChildren(
          r.ok
            ? el('p', { cls: 'notice', text: `${r.service}: ${r.status} ${r.root} (${r.correlation_id})` })
            : errorBox(r.error),
        );
      });
    });
    const item = el(
      'li',
      {},
      el('strong', { text: s.name }),
      el('span', { text: s.resources.join(', ') }),
      probe,
    );
    item.dataset.service = s.name;
    list.append(item);
  }
  const out = el('button', { text: 'Sign out' });
  out.addEventListener('click', () => {
    void api.signOut().then((r) => render(r.state));
  });
  const role = el('p', { text: `Signed in as ${state.role}` });
  role.dataset.testid = 'role';
  root.replaceChildren(
    el('h1', { text: 'MOSAIQ back office' }),
    role,
    el('h2', { text: 'Services' }),
    list,
    result,
    out,
  );
}

function render(state: AppState, error?: ErrorView): void {
  if (state.status === 'signed_in') renderHome(state);
  else renderSignIn(state, error);
}

api.onStateChange((state) => render(state));
void api.getState().then((state) => render(state));
