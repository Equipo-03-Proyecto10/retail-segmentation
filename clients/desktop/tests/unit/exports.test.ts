import { describe, expect, it } from 'vitest';
import { Exporter, csvCell, toCsv, type ExportEnv } from '../../src/main/exports';
import { claims, makeOffice, MemoryVault, tokenResult } from '../support/fake';

async function setup(role: string, choose: string | null = '/tmp/out.csv', failAudit = false) {
  const { office } = makeOffice(
    (r) => {
      if (r.method === 'POST' && r.url.endsWith('/v1/sessions'))
        return { status: 201, body: tokenResult('a1', 'r1') };
      if (r.url.endsWith('/v1/sessions/current'))
        return r.method === 'GET' ? { status: 200, body: claims(role) } : { status: 204 };
      return { status: 500 };
    },
    new MemoryVault({ access: 'a1', refresh: 'r1', expiresAt: Date.parse('2099-01-01') }),
  );
  await office.signIn({ email: 'a@b.c', password: 'pw' });
  const files: Record<string, string> = {};
  const lines: string[] = [];
  const env: ExportEnv = {
    chooseFile: () => Promise.resolve(choose ?? undefined),
    saveCsv: (p, t) => void (files[p] = t),
    appendLine: (_p, l) => {
      if (failAudit) throw new Error('disk full');
      lines.push(l);
    },
    now: () => new Date('2026-11-03T10:00:00Z'),
  };
  return { exporter: new Exporter(office, env, '/cfg'), files, lines };
}

const table = { module: 'audit', columns: ['ID', 'Action'], rows: [['1', 'UPDATE']] };

describe('csv', () => {
  it('quotes, and defuses formulas but not plain numbers', () => {
    expect(csvCell('a,"b"')).toBe('"a,""b"""');
    expect(csvCell('=SUM(A1)')).toBe("'=SUM(A1)");
    expect(csvCell('-12.5')).toBe('-12.5');
    expect(toCsv(['x'], [['1']])).toBe(`${String.fromCharCode(0xfeff)}x\r\n1\r\n`);
  });
});

describe('export', () => {
  it('writes the file and one audit line for a role that may export', async () => {
    const { exporter, files, lines } = await setup('AUDITOR');
    const res = await exporter.exportTable(table);
    expect(res).toEqual({ ok: true, value: { rows: 1, file: '/tmp/out.csv' } });
    expect(files['/tmp/out.csv']).toContain('UPDATE');
    expect(lines).toHaveLength(1);
    expect(JSON.parse(lines[0] as string)).toMatchObject({ role: 'AUDITOR', module: 'audit', rows: 1 });
  });

  it('refuses a role that may not export, writing and auditing nothing', async () => {
    const { exporter, files, lines } = await setup('STORE_MANAGER');
    const res = await exporter.exportTable({ ...table, module: 'sales' });
    expect(res).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
    expect(files).toEqual({});
    expect(lines).toEqual([]);
  });

  it('refuses a module the role cannot read', async () => {
    const { exporter } = await setup('MARKETING');
    expect(await exporter.exportTable(table)).toMatchObject({ ok: false, error: { code: 'forbidden_role' } });
  });

  it('writes nothing when the user cancels the dialog', async () => {
    const { exporter, files, lines } = await setup('AUDITOR', null);
    expect(await exporter.exportTable(table)).toEqual({ ok: true, value: null });
    expect(files).toEqual({});
    expect(lines).toEqual([]);
  });

  it('does not write a file it could not record', async () => {
    const { exporter, files } = await setup('AUDITOR', '/tmp/out.csv', true);
    expect(await exporter.exportTable(table)).toMatchObject({
      ok: false,
      error: { code: 'audit_unavailable' },
    });
    expect(files).toEqual({});
  });

  it('rejects a malformed table', async () => {
    const { exporter } = await setup('AUDITOR');
    expect(await exporter.exportTable({ module: 'audit', columns: ['a'], rows: [['1', '2']] })).toMatchObject(
      {
        ok: false,
        error: { code: 'invalid_input' },
      },
    );
  });
});
