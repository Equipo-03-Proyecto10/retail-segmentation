import type { AppState, ErrorView, MosaiqBridge } from '../shared/api';
import { renderCategories, renderProducts } from './catalog';
import { el, errorBox } from './dom';
import { renderCheckIns, renderEngagement, renderSales, renderStock } from './floor';

declare global {
  interface Window {
    mosaiq: MosaiqBridge;
  }
}

const root = document.getElementById('app') as HTMLElement;
const api = window.mosaiq;

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

const NO_KEYRING =
  'This computer has no secure keyring. Your session is kept in memory only and ends when the app closes.';

/** The warning shown on every screen when tokens cannot be sealed by an OS keyring. */
function storageWarning(state: AppState): Node[] {
  if (state.storage !== 'memory_only') return [];
  const p = el('p', { cls: 'warning', text: NO_KEYRING });
  p.setAttribute('role', 'status');
  p.dataset.testid = 'storage-warning';
  return [p];
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
  const parts: Node[] = [el('h1', { text: 'MOSAIQ back office' }), ...storageWarning(state)];
  if (state.notice !== undefined) {
    const notice = el('p', { cls: 'notice', text: state.notice });
    notice.dataset.testid = 'notice';
    parts.push(notice);
  }
  if (error) parts.push(errorBox(error, ['email', 'password']));
  parts.push(form);
  root.replaceChildren(...parts);
}

type View = 'products' | 'categories' | 'stock' | 'sales' | 'engagement' | 'check-ins' | 'services';

/** Each screen, and the read operation the role must be allowed to call to see it (x-mosaiq-roles). */
const VIEWS: { name: View; label: string; operation: string | null }[] = [
  { name: 'products', label: 'Products', operation: 'listProducts' },
  { name: 'categories', label: 'Categories', operation: 'listCategories' },
  { name: 'stock', label: 'Stock', operation: 'listStockLevels' },
  { name: 'sales', label: 'Sales', operation: 'listSales' },
  { name: 'engagement', label: 'Engagement', operation: 'getEngagementSummary' },
  { name: 'check-ins', label: 'Check-ins', operation: 'listCheckIns' },
  { name: 'services', label: 'Services', operation: null },
];
let view: View = 'products';

function renderServices(host: HTMLElement, state: Extract<AppState, { status: 'signed_in' }>): void {
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
  host.replaceChildren(el('h2', { text: 'Services' }), list, result);
}

function renderHome(state: Extract<AppState, { status: 'signed_in' }>): void {
  const screen = el('div');
  const ctx = { api, can: (operationId: string) => state.operations.includes(operationId) };
  const allowed = VIEWS.filter((v) => v.operation === null || ctx.can(v.operation));
  const show = (next: View): void => {
    view = next;
    nav
      .querySelectorAll('button')
      .forEach((b) => b.setAttribute('aria-current', String(b.dataset.view === next)));
    if (next === 'products') renderProducts(screen, ctx);
    else if (next === 'categories') renderCategories(screen, ctx);
    else if (next === 'stock') renderStock(screen, ctx);
    else if (next === 'sales') renderSales(screen, ctx);
    else if (next === 'engagement') renderEngagement(screen, ctx);
    else if (next === 'check-ins') renderCheckIns(screen, ctx);
    else renderServices(screen, state);
  };
  const nav = el('nav', { cls: 'nav' });
  for (const v of allowed) {
    const b = el('button', { text: v.label });
    b.type = 'button';
    b.dataset.view = v.name;
    b.addEventListener('click', () => show(v.name));
    nav.append(b);
  }
  const out = el('button', { text: 'Sign out' });
  out.addEventListener('click', () => {
    void api.signOut().then((r) => render(r.state));
  });
  const role = el('p', { text: `Signed in as ${state.role}` });
  role.dataset.testid = 'role';
  root.replaceChildren(
    el('h1', { text: 'MOSAIQ back office' }),
    ...storageWarning(state),
    role,
    nav,
    screen,
    out,
  );
  show(allowed.some((v) => v.name === view) ? view : (allowed[0]?.name ?? 'services'));
}

function render(state: AppState, error?: ErrorView): void {
  if (state.status === 'signed_in') renderHome(state);
  else renderSignIn(state, error);
}

api.onStateChange((state) => render(state));
void api.getState().then((state) => render(state));
