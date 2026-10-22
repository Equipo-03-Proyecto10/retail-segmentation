import { describe, expect, it } from 'vitest';
import { ContractDefect } from '../../src/main/transport';
import { AUTH_NS, claims, COMMON_NS, errorXml, makeTransport } from '../support/fake';

describe('transport', () => {
  it('routes by registry, sends XML headers and returns the parsed root', async () => {
    const { transport, seen } = makeTransport(() => ({ status: 200, body: claims('ADMIN') }));
    const res = await transport.call({ method: 'GET', path: '/v1/sessions/current', bearer: 't' });
    expect(res.ok && res.root).toBe('SessionClaims');
    const req = seen[0]!;
    expect(req.url).toBe('http://gw.test/api/auth/v1/sessions/current');
    expect(req.headers.Accept).toBe('application/xml');
    expect(req.headers['X-Correlation-ID']).toMatch(/^[0-9a-f-]{36}$/);
    expect(req.headers.Authorization).toBe('Bearer t');
  });

  it('validates a request body and labels it application/xml', async () => {
    const { transport, seen } = makeTransport(() => ({ status: 204 }));
    await transport.call({
      method: 'POST',
      path: '/v1/sessions/refresh',
      body: { root: 'RefreshRequest', fields: { refresh_token: 'x' } },
    });
    expect(seen[0]!.headers['Content-Type']).toBe('application/xml');
    await expect(
      transport.call({
        method: 'POST',
        path: '/v1/sessions',
        body: { root: 'SignInRequest', fields: { email: 'a@b.c' } },
      }),
    ).rejects.toThrow(/fails its schema/);
    expect(seen).toHaveLength(1);
  });

  it('never calls jwks.json and rejects unregistered resources', async () => {
    const { transport, seen } = makeTransport(() => ({ status: 200 }));
    await expect(transport.call({ method: 'GET', path: '/v1/jwks.json' })).rejects.toThrow(/jwks/);
    await expect(transport.call({ method: 'GET', path: '/v1/nothing' })).rejects.toThrow(/no registered/);
    expect(seen).toHaveLength(0);
  });

  it('refuses a response in a foreign namespace', async () => {
    const foreign = claims('ADMIN').replace(AUTH_NS, 'https://example.test/other');
    const { transport } = makeTransport(() => ({ status: 200, body: foreign }));
    const err = await transport
      .call({ method: 'GET', path: '/v1/sessions/current' })
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ContractDefect);
    expect((err as ContractDefect).service).toBe('auth');
    expect((err as ContractDefect).correlationId).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.stringify(err)).not.toContain('ADMIN');
  });

  it('refuses a response with an extra element', async () => {
    const extra = claims('ADMIN').replace('</SessionClaims>', '<surprise>1</surprise></SessionClaims>');
    const { transport } = makeTransport(() => ({ status: 200, body: extra }));
    const err = await transport
      .call({ method: 'GET', path: '/v1/sessions/current' })
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ContractDefect);
    expect(String((err as ContractDefect).reason)).toMatch(/schema validation/);
  });

  it('refuses an error that is not in the common namespace, and non-XML bodies', async () => {
    const wrong = errorXml('x').replace(COMMON_NS, AUTH_NS);
    for (const body of [wrong, '<html>bad gateway</html>', '<!DOCTYPE a><a/>']) {
      const { transport } = makeTransport(() => ({ status: 502, body }));
      await expect(transport.call({ method: 'GET', path: '/v1/sessions/current' })).rejects.toBeInstanceOf(
        ContractDefect,
      );
    }
  });

  it('reads the error envelope with its details', async () => {
    const details = '<details><field>email</field><code>required</code></details>';
    const { transport } = makeTransport(() => ({
      status: 422,
      body: errorXml('invalid_body', 'bad', details),
    }));
    const res = await transport.call({ method: 'GET', path: '/v1/sessions/current' });
    expect(!res.ok && res.error).toEqual({
      code: 'invalid_body',
      message: 'bad',
      correlation_id: 'srv-1',
      details: [{ field: 'email', code: 'required' }],
    });
  });
});
