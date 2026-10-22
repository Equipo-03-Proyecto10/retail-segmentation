import { execFileSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { loadSettings } from '../../src/main/settings';
import { bundle } from '../support/fake';

const src = new URL('../../src', import.meta.url).pathname;
const script = new URL('../../scripts/bundle-contracts.mjs', import.meta.url).pathname;
const real = new URL('../../../../contracts', import.meta.url).pathname;

function grep(args: string[]): string {
  try {
    return execFileSync('grep', args, { encoding: 'utf8' });
  } catch {
    return '';
  }
}

describe('compliance', () => {
  it('has no application/json in src', () => expect(grep(['-rn', 'application/json', src])).toBe(''));
  it('keeps tokens out of the renderer files', () =>
    expect(grep(['-rlE', 'safeStorage|refresh_token', join(src, 'renderer'), join(src, 'preload')])).toBe(
      '',
    ));
  it('creates one BrowserWindow and never disables webSecurity', () => {
    expect(grep(['-rn', 'new BrowserWindow', src]).trim().split('\n')).toHaveLength(1);
    expect(grep(['-rnE', 'webSecurity|nodeIntegration: true|sandbox: false', src]).replace(/.*/g, '')).toBe(
      '',
    );
  });
  it('exposes no generic transport on the bridge', () => {
    expect(
      grep([
        '-rnE',
        'fetch\\(|exposeInMainWorld\\(.ipcRenderer',
        join(src, 'preload'),
        join(src, 'renderer'),
      ]),
    ).toBe('');
  });
});

describe('settings', () => {
  it('defaults to the local gateway and reads a configured base URL', () => {
    const dir = mkdtempSync(join(tmpdir(), 'mosaiq-settings-'));
    expect(loadSettings(dir).baseUrl).toBe('http://127.0.0.1:8080');
    writeFileSync(join(dir, 'settings.json'), '{"baseUrl":"https://mosaiq.example.test/"}');
    expect(loadSettings(dir).baseUrl).toBe('https://mosaiq.example.test');
    writeFileSync(join(dir, 'settings.json'), '{"baseUrl":"ftp://x"}');
    expect(() => loadSettings(dir)).toThrow();
    rmSync(dir, { recursive: true });
  });
});

describe('bundle-contracts', () => {
  it('bundles every registered service with its roles', () => {
    expect(bundle.services).toHaveLength(15);
    expect(bundle.skipped).toEqual([]);
    expect(Object.keys(bundle.schemas)).toContain('https://mosaiq.maxthecoder.online/xml/common/v1');
    expect(bundle.operations.find((o) => o.operationId === 'createSession')?.roles).toBeNull();
  });

  function run(contracts: string): { status: number; out: string } {
    const out = mkdtempSync(join(tmpdir(), 'mosaiq-out-'));
    try {
      const stdout = execFileSync('node', [script], {
        env: { ...process.env, MOSAIQ_CONTRACTS_DIR: contracts, MOSAIQ_BUNDLE_DIR: out },
        encoding: 'utf8',
      });
      return { status: 0, out: stdout };
    } catch (e) {
      const err = e as { status: number; stderr: string };
      return { status: err.status, out: err.stderr };
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  }

  it('skips a registry entry whose contract has not merged and fails on a missing xsd', () => {
    const tmp = mkdtempSync(join(tmpdir(), 'mosaiq-contracts-'));
    cpSync(real, tmp, { recursive: true });
    rmSync(join(tmp, 'campaigns'), { recursive: true });
    const skipped = run(tmp);
    expect(skipped.status).toBe(0);
    expect(skipped.out).toMatch(/skipped.*campaigns/);
    mkdirSync(join(tmp, 'campaigns'));
    writeFileSync(join(tmp, 'campaigns', 'openapi.yaml'), 'openapi: 3.1.0\npaths: {}\n');
    const failed = run(tmp);
    expect(failed.status).toBe(1);
    expect(failed.out).toMatch(/campaigns has an openapi.yaml but no campaigns.xsd/);
    rmSync(tmp, { recursive: true, force: true });
  });
});
