import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import { createInterface } from 'node:readline';
import { parseArgs } from 'node:util';
import { loadBundle } from '../src/main/bundle';
import { loadSettings } from '../src/main/settings';
import { collect, STEPS, verdict, type Account, type Step } from './f20-02-evidence';

// npm run evidence:f20-02 -- --out <dir> --sale-id <id> [--config-dir <dir>]
//   [--name <campaign name>] [--label <label_code>] [--starts-on YYYY-MM-DD] [--ends-on YYYY-MM-DD]
//
// The base URL is the app's own: settings.json in the configuration directory
// (MOSAIQ_DESKTOP_CONFIG_DIR, else the platform's), as clients/desktop/README.md says.
// Accounts come from MOSAIQ_F20_02_<STEP>_EMAIL / _PASSWORD, else a prompt;
// a password is never echoed, never an argument, and never written.

const ROOT = resolve(__dirname, '..');

function defaultConfigDir(): string {
  if (process.env.MOSAIQ_DESKTOP_CONFIG_DIR) return process.env.MOSAIQ_DESKTOP_CONFIG_DIR;
  if (process.platform === 'darwin') return join(homedir(), 'Library/Application Support/mosaiq-desktop');
  if (process.platform === 'win32') return join(process.env.APPDATA ?? homedir(), 'mosaiq-desktop');
  return join(process.env.XDG_CONFIG_HOME ?? join(homedir(), '.config'), 'mosaiq-desktop');
}

function ask(prompt: string, hidden: boolean): Promise<string> {
  const rl = createInterface({ input: process.stdin, output: process.stderr, terminal: true });
  if (hidden) {
    const echo = rl as unknown as { _writeToOutput: (s: string) => void };
    echo._writeToOutput = (s: string) => {
      if (s.startsWith(prompt)) process.stderr.write(prompt);
    };
  }
  return new Promise((done) =>
    rl.question(prompt, (answer) => {
      rl.close();
      if (hidden) process.stderr.write('\n');
      done(answer);
    }),
  );
}

async function account(step: Step): Promise<Account> {
  const key = `MOSAIQ_F20_02_${step.toUpperCase()}`;
  const email = process.env[`${key}_EMAIL`] || (await ask(`${step} account email: `, false)).trim();
  const password = process.env[`${key}_PASSWORD`] || (await ask(`${step} account password: `, true));
  return { email, password };
}

function git(...args: string[]): string {
  try {
    return execFileSync('git', ['-C', ROOT, ...args], { encoding: 'utf8' }).trim();
  } catch {
    return '(unavailable)';
  }
}

async function main(): Promise<number> {
  const { values } = parseArgs({
    options: {
      out: { type: 'string' },
      'sale-id': { type: 'string' },
      'config-dir': { type: 'string' },
      name: { type: 'string' },
      label: { type: 'string', default: 'CHAMPION' },
      'starts-on': { type: 'string' },
      'ends-on': { type: 'string' },
    },
  });
  if (!values.out || !values['sale-id']) {
    process.stderr.write('usage: npm run evidence:f20-02 -- --out <dir> --sale-id <id> [options]\n');
    return 2;
  }
  const configDir = values['config-dir'] ?? defaultConfigDir();
  const { baseUrl } = loadSettings(configDir);
  const today = new Date();
  const day = (offset: number): string =>
    new Date(today.getTime() + offset * 86_400_000).toISOString().slice(0, 10);
  const stamp = today.toISOString().replace(/[-:]/g, '').slice(0, 13);
  const accounts = {} as Record<Step, Account>;
  for (const step of STEPS) accounts[step] = await account(step);
  const pkg = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8')) as { version: string };
  const out = resolve(values.out);
  const evidence = await collect({
    bundle: loadBundle(join(ROOT, 'bundled')),
    baseUrl,
    out,
    saleId: Number(values['sale-id']),
    draft: {
      name: values.name ?? `F20-02 evidence draft ${stamp}Z`,
      label_code: values.label ?? 'CHAMPION',
      starts_on: values['starts-on'] ?? day(7),
      ends_on: values['ends-on'] ?? day(37),
    },
    accounts,
    build: {
      'desktop version': pkg.version,
      'checkout commit': git('rev-parse', 'HEAD'),
      'checkout describe': git('describe', '--always', '--dirty', '--tags'),
      'settings.json': join(configDir, 'settings.json'),
    },
  });
  for (const step of STEPS) {
    const s = evidence.steps[step];
    process.stdout.write(
      `${step}: ${s.account} (${s.role ?? 'not signed in'}) → ${s.status ?? '-'} ` +
        `${s.error?.code ?? 'ok'}, X-Correlation-ID ${s.correlation_id ?? '-'}\n`,
    );
  }
  const failures = verdict(evidence);
  for (const f of failures) process.stdout.write(`NOT HELD: ${f}\n`);
  process.stdout.write(`${join(out, 'result.json')}\n`);
  return failures.length === 0 ? 0 : 1;
}

main().then(
  (code) => process.exit(code),
  (e: unknown) => {
    process.stderr.write(`${e instanceof Error ? e.message : String(e)}\n`);
    process.exit(1);
  },
);
