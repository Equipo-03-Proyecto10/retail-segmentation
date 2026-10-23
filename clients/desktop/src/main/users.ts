import type { Paged, Result, RoleView, UserView } from '../shared/api';
import { localError, type Backoffice } from './backoffice';
import {
  checked,
  failed,
  FieldChecks,
  invalidInput,
  optionalBoolean,
  optionalText,
  pageNumber,
  pageOf,
  pageSize,
  record,
  Refused,
  uuid,
} from './checks';
import type { CallResult } from './transport';
import type { FieldValue } from './xml';

// User administration and the administrator transfer (F16-06, #484). Every value
// from the screens is checked here before a path or a body is built from it. The
// single-administrator rule is users-service's, and the schema's: nothing here
// decides who may be an administrator, so every refusal is the service's answer.

const NAME_MAX = 120;
const EMAIL_MAX = 160;
const ROLE_CODE_MAX = 40;
const PASSWORD_MIN = 12;
const EMAIL = /^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$/;
const ROLE_CODE = /^[A-Z][A-Z0-9_]*$/;

const truthy = (v: string | undefined): boolean => v === 'true' || v === '1';

interface UserXml {
  user_id: string;
  name: string;
  email: string;
  role_code: string;
  is_active: string;
  created_at: string;
}

interface RoleXml {
  role_code: string;
  description: string;
}

function userOf(x: UserXml): UserView {
  return {
    user_id: x.user_id,
    name: x.name,
    email: x.email,
    role_code: x.role_code,
    is_active: truthy(x.is_active),
    created_at: x.created_at,
  };
}

/** Field checks for the users contract; the codes are the ones the service names. */
class UserChecks extends FieldChecks {
  email(field: string, value: unknown): string {
    const v = this.text(field, value, EMAIL_MAX);
    if (v !== '' && !EMAIL.test(v) && !this.details.some((d) => d.field === field))
      this.details.push({ field, code: 'invalid_format' });
    return v;
  }

  roleCode(field: string, value: unknown): string {
    const v = this.text(field, value, ROLE_CODE_MAX);
    if (v !== '' && !ROLE_CODE.test(v) && !this.details.some((d) => d.field === field))
      this.details.push({ field, code: 'invalid_format' });
    return v;
  }

  /** At least 12 characters, counted as the XSD counts them. Never echoed back. */
  password(field: string, value: unknown): string {
    if (typeof value !== 'string' || value === '') {
      this.details.push({ field, code: 'required' });
      return '';
    }
    if ([...value].length < PASSWORD_MIN) this.details.push({ field, code: 'min_length' });
    return value;
  }
}

export class Users {
  constructor(private readonly office: Backoffice) {}

  private run<T>(work: () => Promise<Result<T>>): Promise<Result<T>> {
    return checked(this.office, work);
  }

  listUsers(query: unknown): Promise<Result<Paged<UserView>>> {
    return this.run(async () => {
      const q = record(query ?? {}, 'The search');
      const params = new URLSearchParams();
      const role = optionalText(q.role, 'The role');
      if (role) {
        if (!ROLE_CODE.test(role) || role.length > ROLE_CODE_MAX)
          throw invalidInput('The role is not a role code.');
        params.set('role', role);
      }
      const active = optionalBoolean(q.active, 'Active');
      if (active !== undefined) params.set('active', String(active));
      const text = optionalText(q.q, 'The search text');
      if (text?.trim()) params.set('q', text.trim());
      params.set('page', String(pageNumber(q.page)));
      params.set('size', String(pageSize(q.size)));
      const res = await this.office.authed({ method: 'GET', path: `/v1/users?${params.toString()}` });
      if (!res.ok) return failed(res);
      const data = res.data?.Users as { User?: UserXml[] } & Record<string, unknown>;
      return { ok: true, value: { items: (data.User ?? []).map(userOf), page: pageOf(data) } };
    });
  }

  getUser(userId: unknown): Promise<Result<UserView>> {
    return this.run(async () => {
      const uid = uuid(userId, 'The user');
      const res = await this.office.authed({ method: 'GET', path: `/v1/users/${encodeURIComponent(uid)}` });
      return this.userResult(res);
    });
  }

  /** The role vocabulary (E-02). Seven codes, so one page holds them all. */
  listRoles(): Promise<Result<RoleView[]>> {
    return this.run(async () => {
      const res = await this.office.authed({ method: 'GET', path: '/v1/roles' });
      if (!res.ok) return failed(res);
      const data = res.data?.Roles as { Role?: RoleXml[] };
      return {
        ok: true,
        value: (data.Role ?? []).map((r) => ({ role_code: r.role_code, description: r.description })),
      };
    });
  }

  createUser(draft: unknown): Promise<Result<UserView>> {
    return this.run(async () => {
      const d = record(draft, 'The user');
      const f = new UserChecks();
      const fields: Record<string, FieldValue> = {
        name: f.text('name', d.name, NAME_MAX),
        email: f.email('email', d.email),
        role_code: f.roleCode('role_code', d.role_code),
        password: f.password('password', d.password),
      };
      f.done();
      const res = await this.office.authed({
        method: 'POST',
        path: '/v1/users',
        body: { root: 'CreateUserRequest', fields },
      });
      return this.userResult(res);
    });
  }

  /** Only the fields that changed are sent. A role change to or from ADMIN is the service's to refuse. */
  updateUser(userId: unknown, changes: unknown): Promise<Result<UserView>> {
    return this.run(async () => {
      const uid = uuid(userId, 'The user');
      const c = record(changes, 'The changes');
      const f = new UserChecks();
      // The XSD fixes this order: name, email, role_code.
      const fields: Record<string, FieldValue> = {};
      if (c.name !== undefined) fields.name = f.text('name', c.name, NAME_MAX);
      if (c.email !== undefined) fields.email = f.email('email', c.email);
      if (c.role_code !== undefined) fields.role_code = f.roleCode('role_code', c.role_code);
      f.done();
      if (Object.keys(fields).length === 0) throw invalidInput('Nothing was changed.');
      const res = await this.office.authed({
        method: 'PATCH',
        path: `/v1/users/${encodeURIComponent(uid)}`,
        body: { root: 'UpdateUserRequest', fields },
      });
      return this.userResult(res);
    });
  }

  /** Accounts are deactivated, never deleted (RN-04): there is no delete here. */
  deactivateUser(userId: unknown): Promise<Result<UserView>> {
    return this.run(async () => {
      const uid = uuid(userId, 'The user');
      const res = await this.office.authed({
        method: 'POST',
        path: `/v1/users/${encodeURIComponent(uid)}/deactivation`,
      });
      return this.userResult(res);
    });
  }

  reactivateUser(userId: unknown): Promise<Result<UserView>> {
    return this.run(async () => {
      const uid = uuid(userId, 'The user');
      const res = await this.office.authed({
        method: 'DELETE',
        path: `/v1/users/${encodeURIComponent(uid)}/deactivation`,
      });
      return this.userResult(res);
    });
  }

  /** An administrator's reset of another user's password: no current password is sent. */
  resetUserPassword(userId: unknown, newPassword: unknown): Promise<Result<null>> {
    return this.run(async () => {
      const uid = uuid(userId, 'The user');
      const f = new UserChecks();
      const fields = { new_password: f.password('new_password', newPassword) };
      f.done();
      return this.putPassword(uid, fields);
    });
  }

  /** The caller's own password, with the current one. Success ends every session, this one included. */
  changeOwnPassword(currentPassword: unknown, newPassword: unknown): Promise<Result<null>> {
    return this.run(async () => {
      const own = this.office.userId();
      if (own === null) throw new Refused(localError('signed_out', 'You are signed out.'));
      const f = new UserChecks();
      const fields = {
        current_password: f.password('current_password', currentPassword),
        new_password: f.password('new_password', newPassword),
      };
      f.done();
      return this.putPassword(own, fields);
    });
  }

  private async putPassword(uid: string, fields: Record<string, FieldValue>): Promise<Result<null>> {
    const res = await this.office.authed({
      method: 'PUT',
      path: `/v1/users/${encodeURIComponent(uid)}/password`,
      body: { root: 'PasswordChangeRequest', fields },
    });
    if (!res.ok) return failed(res);
    await this.office.passwordChanged(uid);
    return { ok: true, value: null };
  }

  /** The service demotes and promotes in one transaction; the next call is refused with session_revoked. */
  transferAdministrator(successorUserId: unknown): Promise<Result<null>> {
    return this.run(async () => {
      const f = new UserChecks();
      if (typeof successorUserId !== 'string' || successorUserId === '')
        f.details.push({ field: 'successor_user_id', code: 'required' });
      f.done();
      const successor = uuid(successorUserId, 'The successor');
      const res = await this.office.authed({
        method: 'POST',
        path: '/v1/administrator-transfers',
        body: { root: 'AdministratorTransferRequest', fields: { successor_user_id: successor } },
      });
      if (!res.ok) return failed(res);
      return { ok: true, value: null };
    });
  }

  private userResult(res: CallResult): Result<UserView> {
    if (!res.ok) return failed(res);
    return { ok: true, value: userOf(res.data?.User as UserXml) };
  }
}
