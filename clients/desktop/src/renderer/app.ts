import type { AppState, ErrorView, MosaiqBridge } from '../shared/api';
import { visibleGroups, type ModuleName } from '../shared/modules';
import { renderAudit } from './audit';
import { renderCampaigns } from './campaigns';
import { renderCategories, renderProducts } from './catalog';
import { el, errorBox } from './dom';
import { renderConversion } from './conversion';
import { renderCustomers } from './customers';
import { renderExploration } from './exploration';
import { renderDrift } from './drift';
import { renderExperiments } from './experiments';
import { renderKmeans } from './kmeans';
import { renderImports } from './imports';
import { renderCheckIns, renderEngagement, renderSales, renderStock } from './floor';
import { latestRequest } from './latest-request';
import { renderMigrations } from './migrations';
import { exportAction } from './export';
import { guardBridge } from './session';
import { reconcileRender, type RenderState } from './render-state';
import { renderRecommendations } from './recommendations';
import { renderReports } from './reports';
import { renderStores } from './stores';
import { renderAccount, renderTransfer, renderUsers } from './users';

declare global {
  interface Window {
    mosaiq: MosaiqBridge;
  }
}

const root = document.getElementById('app') as HTMLElement;
const busy = el('p', { cls: 'busy', text: 'Loading…' });
busy.setAttribute('role', 'status');
busy.dataset.testid = 'loading';
busy.hidden = true;
const api = guardBridge(window.mosaiq, {
  onBusy: (on) => {
    busy.hidden = !on;
  },
  // The main process has already ended the session; show what it now says (sign-in and the reason).
  onSessionEnded: () => void window.mosaiq.getState().then((state) => render(state)),
});

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

let view: ModuleName = 'products';

function renderServices(host: HTMLElement, state: Extract<AppState, { status: 'signed_in' }>): void {
  const result = el('div');
  const renderLatest = latestRequest();
  const list = el('ul', { cls: 'services' });
  for (const s of state.services) {
    const probe = el('button', { text: 'Check' });
    probe.addEventListener('click', () => {
      const isCurrent = renderLatest.begin();
      void api.probeService(s.name).then((r) => {
        if (!isCurrent()) return;
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
  const groups = visibleGroups(state.operations);
  const allowed = groups.flatMap((g) => g.modules);
  const nav = el('nav', { cls: 'nav' });
  nav.setAttribute('aria-label', 'Modules');
  const show = (next: ModuleName): void => {
    view = next;
    nav
      .querySelectorAll('button')
      .forEach((b) => b.setAttribute('aria-current', String(b.dataset.view === next)));
    const body = el('div');
    if (next === 'products') renderProducts(body, ctx);
    else if (next === 'categories') renderCategories(body, ctx);
    else if (next === 'stock') renderStock(body, ctx);
    else if (next === 'sales') renderSales(body, ctx);
    else if (next === 'engagement') renderEngagement(body, ctx);
    else if (next === 'check-ins') renderCheckIns(body, ctx);
    else if (next === 'migrations') renderMigrations(body, ctx);
    else if (next === 'reports') renderReports(body, { ...ctx, canExport: state.canExport });
    else if (next === 'recommendations') renderRecommendations(body, ctx);
    else if (next === 'drift') renderDrift(body, ctx);
    else if (next === 'conversion') renderConversion(body, ctx);
    else if (next === 'kmeans') renderKmeans(body, ctx);
    else if (next === 'imports') renderImports(body, ctx);
    else if (next === 'stores') renderStores(body, ctx);
    else if (next === 'campaigns') renderCampaigns(body, ctx);
    else if (next === 'experiments') renderExperiments(body, ctx);
    else if (next === 'customers') renderCustomers(body, ctx);
    else if (next === 'exploration') renderExploration(body, ctx);
    else if (next === 'users') renderUsers(body, ctx);
    else if (next === 'audit') renderAudit(body, ctx);
    else if (next === 'transfer') renderTransfer(body, ctx);
    else renderServices(body, state);
    screen.replaceChildren(
      ...(state.canExport && next !== 'reports' ? [exportAction(api, next, body)] : []),
      body,
    );
  };
  for (const g of groups) {
    const group = el('div', { cls: 'nav-group' }, el('strong', { text: g.label }));
    group.dataset.group = g.id;
    for (const m of g.modules) {
      const b = el('button', { text: m.label });
      b.type = 'button';
      b.dataset.view = m.name;
      b.addEventListener('click', () => show(m.name));
      group.append(b);
    }
    nav.append(group);
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
    renderAccount(ctx),
    nav,
    busy,
    screen,
    out,
  );
  show(allowed.some((m) => m.name === view) ? view : (allowed[0]?.name ?? 'services'));
}

let rendered: RenderState | undefined;

function render(state: AppState, error?: ErrorView): void {
  rendered = reconcileRender(rendered, state, error);
  if (rendered.state.status === 'signed_in') renderHome(rendered.state);
  else renderSignIn(rendered.state, rendered.error);
}

api.onStateChange((state) => render(state));
void api.getState().then((state) => render(state));
