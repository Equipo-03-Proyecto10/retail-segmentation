import type { RoleView, UserQuery, UserView } from '../shared/api';
import type { ScreenContext } from './catalog';
import {
  button,
  el,
  errorBox,
  formField,
  markDetails,
  pager,
  select,
  show,
  testid,
  textInput,
  type Details,
} from './dom';

// User administration, the administrator transfer and the account menu (F16-06,
// #484). A control is drawn only for a role that may call its operation; the
// service decides. The single-administrator rule is never worked out here:
// every refusal (single_administrator, use_administrator_transfer,
// administrator_required, ...) is shown as users-service returned it.

const ADMIN = 'ADMIN';
const USER_FIELDS = ['name', 'email', 'role_code', 'password', 'new_password', 'current_password'];

const SIZES: [string, string][] = [
  ['20', '20 a page'],
  ['50', '50 a page'],
  ['100', '100 a page'],
];

function submit(text: string): HTMLButtonElement {
  const b = el('button', { text });
  b.type = 'submit';
  return b;
}

function passwordInput(autocomplete: 'new-password' | 'current-password'): HTMLInputElement {
  const input = textInput('', 'password');
  input.autocomplete = autocomplete;
  return input;
}

function table(id: string, headers: string[], rows: HTMLTableRowElement[]): HTMLTableElement {
  const head = el('tr');
  for (const h of headers) head.append(el('th', { text: h }));
  const body = el('tbody');
  body.append(...rows);
  return testid(el('table', {}, el('thead', {}, head), body), id);
}

/** Roles a person may be given by hand: every role except ADMIN, which moves only by transfer. */
function assignable(roles: RoleView[]): [string, string][] {
  return roles.filter((r) => r.role_code !== ADMIN).map((r) => [r.role_code, r.role_code]);
}

/** Shows a result; a refusal to move the ADMIN role by hand also points to the transfer screen. */
function showUserResult(
  area: HTMLElement,
  res: Parameters<typeof show>[1],
  success: string,
  fields: string[] = USER_FIELDS,
): void {
  show(area, res, success, fields);
  if (!res.ok && res.error.code === 'use_administrator_transfer') {
    area.append(
      testid(
        el('p', {
          cls: 'notice',
          text: 'The administrator role moves only through the Transfer administrator screen.',
        }),
        'transfer-hint',
      ),
    );
  }
}

// ---------- users ----------

export function renderUsers(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const message = testid(el('div'), 'users-message');
  const tableArea = el('div');
  const detail = testid(el('section', { cls: 'panel' }), 'user-detail');
  let roles: RoleView[] = [];
  let query: UserQuery = { page: 1, size: 20 };

  const q = textInput('', 'search');
  const role = select([['', 'Any role']], '');
  const active = select(
    [
      ['', 'Active or not'],
      ['true', 'Active'],
      ['false', 'Inactive'],
    ],
    '',
  );
  const size = select(SIZES, '20');
  const filters = testid(
    el(
      'form',
      { cls: 'filters' },
      formField('q', 'Name or email contains', q, [], 'filter'),
      formField('role', 'Role', role, [], 'filter'),
      formField('active', 'Status', active, [], 'filter'),
      formField('size', 'Page size', size, [], 'filter'),
      submit('Search'),
    ),
    'user-filters',
  );
  filters.noValidate = true;
  filters.addEventListener('submit', (event) => {
    event.preventDefault();
    query = { page: 1, size: Number(size.value) };
    if (q.value.trim()) query.q = q.value.trim();
    if (role.value) query.role = role.value;
    if (active.value) query.active = active.value === 'true';
    void load();
  });

  async function load(): Promise<void> {
    const res = await api.listUsers(query);
    if (!res.ok) return void tableArea.replaceChildren(errorBox(res.error));
    const rows = res.value.items.map((u) => {
      const tr = el(
        'tr',
        {},
        el('td', { text: u.name }),
        el('td', { text: u.email }),
        el('td', { text: u.role_code }),
        el('td', { text: u.is_active ? 'Active' : 'Inactive' }),
        el(
          'td',
          {},
          button('View', `view-user-${u.user_id}`, () => void open(u.user_id)),
        ),
      );
      tr.dataset.userId = u.user_id;
      return tr;
    });
    tableArea.replaceChildren(
      table('users-table', ['Name', 'Email', 'Role', 'Status', ''], rows),
      pager(res.value.page, 'users', (page) => {
        query = { ...query, page };
        void load();
      }),
    );
  }

  async function open(userId: string): Promise<void> {
    await rolesLoaded;
    const res = await api.getUser(userId);
    if (!res.ok) return void detail.replaceChildren(errorBox(res.error));
    showUser(res.value);
  }

  function showUser(u: UserView): void {
    const facts = el('dl');
    const fact = (name: string, value: string, id: string): void => {
      facts.append(el('dt', { text: name }), testid(el('dd', { text: value }), id));
    };
    fact('Name', u.name, 'user-name');
    fact('Email', u.email, 'user-email');
    fact('Role', u.role_code, 'user-role');
    fact('Status', u.is_active ? 'Active' : 'Inactive', 'user-status');
    fact('Created', u.created_at, 'user-created');
    const parts: Node[] = [el('h3', { text: u.name }), facts];
    // AUDITOR reads only. Accounts are deactivated, never deleted: there is no delete control.
    if (ctx.can('updateUser')) parts.push(editForm(u));
    if (ctx.can('deactivateUser')) parts.push(activationControl(u));
    if (ctx.can('createUser')) parts.push(resetForm(u));
    detail.replaceChildren(...parts);
  }

  function editForm(u: UserView): HTMLElement {
    const name = textInput(u.name);
    const email = textInput(u.email, 'email');
    const options = assignable(roles);
    // The ADMIN keeps the role shown; picking another one is for the service to refuse.
    if (u.role_code === ADMIN) options.unshift([ADMIN, ADMIN]);
    const picked = select(options, u.role_code);
    const form = testid(
      el(
        'form',
        {},
        el('h4', { text: 'Edit' }),
        formField('name', 'Name', name, []),
        formField('email', 'Email', email, []),
        formField('role_code', 'Role', picked, []),
        submit('Save changes'),
      ),
      'user-edit',
    );
    form.noValidate = true;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const changes: Record<string, string> = {};
      if (name.value !== u.name) changes.name = name.value;
      if (email.value !== u.email) changes.email = email.value;
      if (picked.value !== u.role_code) changes.role_code = picked.value;
      void api.updateUser(u.user_id, changes).then(async (res) => {
        showUserResult(message, res, 'User saved.');
        if (res.ok) showUser(res.value);
        else markDetails(form, res.error.details);
        await load();
      });
    });
    return form;
  }

  function activationControl(u: UserView): HTMLElement {
    const area = el('div');
    if (!u.is_active) {
      area.append(
        button('Reactivate', 'reactivate', () => {
          void api.reactivateUser(u.user_id).then(async (res) => {
            showUserResult(message, res, 'User reactivated.');
            if (res.ok) showUser(res.value);
            await load();
          });
        }),
      );
      return area;
    }
    const confirm = button('Confirm deactivation', 'confirm-deactivate', () => {
      void api.deactivateUser(u.user_id).then(async (res) => {
        showUserResult(message, res, 'User deactivated.');
        if (res.ok) showUser(res.value);
        await load();
      });
    });
    area.append(
      button('Deactivate', 'deactivate', () => {
        area.replaceChildren(
          el('p', { text: 'The account stays listed, marked inactive, and its sessions end.' }),
          confirm,
        );
      }),
    );
    return area;
  }

  function resetForm(u: UserView): HTMLElement {
    const next = passwordInput('new-password');
    const form = testid(
      el(
        'form',
        {},
        el('h4', { text: 'Reset password' }),
        formField('new_password', 'New password (12 characters or more)', next, []),
        submit('Reset password'),
      ),
      'user-reset',
    );
    form.noValidate = true;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const typed = next.value;
      next.value = '';
      void api.resetUserPassword(u.user_id, typed).then((res) => {
        showUserResult(message, res, 'Password reset. It is not shown.');
        if (!res.ok) markDetails(form, res.error.details);
      });
    });
    return form;
  }

  function newUserForm(values: Record<string, string> = {}, details: Details = []): HTMLElement {
    const name = textInput(values.name ?? '');
    const email = textInput(values.email ?? '', 'email');
    const picked = select(assignable(roles), values.role_code ?? assignable(roles)[0]?.[0] ?? '');
    const password = passwordInput('new-password');
    const form = testid(
      el(
        'form',
        {},
        el('h3', { text: 'New user' }),
        formField('name', 'Name', name, details),
        formField('email', 'Email', email, details),
        formField('role_code', 'Role', picked, details),
        formField('password', 'Password (12 characters or more)', password, details),
        submit('Create user'),
      ),
      'user-new',
    );
    form.noValidate = true;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const typed = { name: name.value, email: email.value, role_code: picked.value };
      void api.createUser({ ...typed, password: password.value }).then(async (res) => {
        showUserResult(message, res, 'User created.');
        // The password is never put back on screen.
        if (res.ok) showUser(res.value);
        else detail.replaceChildren(newUserForm(typed, res.error.details));
        await load();
      });
    });
    return form;
  }

  const header: Node[] = [el('h2', { text: 'Users' })];
  if (ctx.can('createUser'))
    header.push(
      button('New user', 'new-user', () => {
        message.replaceChildren();
        // The role picker is filled from users-service, so the form waits for the roles.
        void rolesLoaded.then(() => detail.replaceChildren(newUserForm()));
      }),
    );
  host.replaceChildren(...header, filters, message, tableArea, detail);

  const rolesLoaded = api.listRoles().then((res) => {
    if (!res.ok) return void message.replaceChildren(errorBox(res.error));
    roles = res.value;
    for (const r of roles)
      role.append(Object.assign(el('option', { text: r.role_code }), { value: r.role_code }));
  });
  void load();
}

// ---------- administrator transfer ----------

export function renderTransfer(host: HTMLElement, ctx: ScreenContext): void {
  const { api } = ctx;
  const message = testid(el('div'), 'transfer-message');
  const successor = select([['', 'Choose an active user']], '');
  const search = textInput('', 'search');
  const dialog = testid(el('dialog'), 'transfer-dialog');

  async function loadCandidates(): Promise<void> {
    const res = await api.listUsers({
      active: true,
      size: 100,
      ...(search.value.trim() ? { q: search.value.trim() } : {}),
    });
    if (!res.ok) return void message.replaceChildren(errorBox(res.error));
    successor.replaceChildren(
      Object.assign(el('option', { text: 'Choose an active user' }), { value: '' }),
      // The incumbent is the one ADMIN in the list; the service still checks the successor.
      ...res.value.items
        .filter((u) => u.role_code !== ADMIN)
        .map((u) =>
          Object.assign(el('option', { text: `${u.name} <${u.email}> (${u.role_code})` }), {
            value: u.user_id,
          }),
        ),
    );
  }

  const find = testid(
    el(
      'form',
      { cls: 'filters' },
      formField('q', 'Name or email contains', search, [], 'successor'),
      submit('Find'),
    ),
    'successor-search',
  );
  find.noValidate = true;
  find.addEventListener('submit', (event) => {
    event.preventDefault();
    void loadCandidates();
  });

  function confirmTransfer(userId: string, label: string): void {
    const confirm = button('Confirm transfer', 'confirm-transfer', () => {
      dialog.close();
      void api.transferAdministrator(userId).then(async (res) => {
        showUserResult(
          message,
          res,
          'The administrator role was handed over. Both sessions end, so you are signed out.',
          ['successor_user_id'],
        );
        if (res.ok) {
          // The next call is refused with session_revoked, which returns the app to sign-in.
          await api.listUsers({ role: ADMIN });
        } else {
          markDetails(form, res.error.details);
        }
      });
    });
    const cancel = button('Cancel', 'cancel-transfer', () => dialog.close());
    dialog.replaceChildren(
      el('h3', { text: 'Transfer the administrator role' }),
      el('p', { text: `${label} becomes the administrator and you take their previous role.` }),
      testid(
        el('p', {
          cls: 'warning',
          text: 'This ends the sessions of both accounts. You and the successor must sign in again.',
        }),
        'transfer-warning',
      ),
      confirm,
      cancel,
    );
    dialog.showModal();
  }

  const form = testid(
    el(
      'form',
      {},
      formField('successor_user_id', 'Successor', successor, [], 'transfer'),
      submit('Transfer the administrator role'),
    ),
    'transfer-form',
  );
  form.noValidate = true;
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const chosen = successor.selectedOptions[0];
    // An empty choice is refused before sending, naming the field.
    confirmTransfer(successor.value, chosen?.textContent ?? 'The chosen user');
  });

  host.replaceChildren(
    el('h2', { text: 'Transfer administrator' }),
    el('p', {
      text: 'The system has exactly one administrator. The role moves only here, to an active user.',
    }),
    find,
    message,
    form,
    dialog,
  );
  void loadCandidates();
}

// ---------- account menu ----------

/** Any staff role changes its own password here; the service ends every session of the user. */
export function renderAccount(ctx: ScreenContext): HTMLElement {
  const message = testid(el('div'), 'account-message');
  const current = passwordInput('current-password');
  const next = passwordInput('new-password');
  const form = testid(
    el(
      'form',
      {},
      formField('current_password', 'Current password', current, [], 'account'),
      formField('new_password', 'New password (12 characters or more)', next, [], 'account'),
      submit('Change password'),
    ),
    'account-form',
  );
  form.noValidate = true;
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const typed = { current: current.value, next: next.value };
    current.value = '';
    next.value = '';
    // On success the app returns to sign-in with "Password changed, sign in again."
    void ctx.api.changeOwnPassword(typed.current, typed.next).then((res) => {
      showUserResult(message, res, 'Password changed.', ['current_password', 'new_password']);
      if (!res.ok) markDetails(form, res.error.details);
    });
  });
  const menu = testid(
    el('details', { cls: 'account' }, el('summary', { text: 'Account' }), form, message),
    'account-menu',
  );
  return menu;
}
