import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

// #1028: a window test makes its profile (and any other scratch directory) only through
// tests/window/electron-test.ts, which removes it when the test ends. This scans every file under
// tests/window except the helper, so a spec that launches Electron or makes a directory itself fails here.

const WINDOW_TESTS = join(__dirname, '..', 'window');
const HELPER = 'electron-test.ts';

/** Ways to make a directory or launch Electron that skip the helper. */
const OWN_PROFILE =
  /\b(?:mkdtemp(?:Sync)?|tmpdir)\s*\(|MOSAIQ_DESKTOP_CONFIG_DIR|\b_?electron\.launch\s*\(|\b_electron\b/;

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sources(path);
    return entry.name.endsWith('.ts') && entry.name !== HELPER ? [path] : [];
  });
}

/** Imports from '@playwright/test' that bring a value: only `type X` specifiers are fine in a spec. */
function valueImportsFromPlaywright(text: string): string[] {
  const found: string[] = [];
  for (const match of text.matchAll(/import\s+(?!type\b)\{([^}]*)\}\s+from\s+'@playwright\/test'/g)) {
    for (const specifier of (match[1] ?? '').split(',').map((s) => s.trim())) {
      if (specifier !== '' && !specifier.startsWith('type ')) found.push(specifier);
    }
  }
  return found;
}

export function profileOffenders(files: Array<{ name: string; text: string }>): string[] {
  return files.flatMap(({ name, text }) => {
    const problems: string[] = [];
    if (OWN_PROFILE.test(text)) problems.push(`${name}: makes its own profile or launches Electron itself`);
    const values = valueImportsFromPlaywright(text);
    if (values.length > 0)
      problems.push(`${name}: imports ${values.join(', ')} from '@playwright/test', not './electron-test'`);
    return problems;
  });
}

describe('window test profile ownership', () => {
  it('keeps temporary directories and Electron launches in the shared helper', () => {
    const files = sources(WINDOW_TESTS).map((path) => ({
      name: relative(WINDOW_TESTS, path),
      text: readFileSync(path, 'utf8'),
    }));

    expect(files.length).toBeGreaterThan(15);
    expect(profileOffenders(files)).toEqual([]);
  });

  it('fails a spec that makes its own profile, launches Electron itself or skips the helper fixture', () => {
    const own = `import { mkdtempSync } from 'node:fs';\nconst d = mkdtempSync(join(tmpdir(), 'mosaiq-desktop-'));`;
    const env = `const env = { MOSAIQ_DESKTOP_CONFIG_DIR: dir };`;
    const launch = `import { _electron as electron, expect, test } from '@playwright/test';\nawait electron.launch({});`;
    const plain = `import { expect, test, type Page } from '@playwright/test';`;
    const fine = `import { expect, launchElectron, test, type Page } from './electron-test';`;

    expect(profileOffenders([{ name: 'a.spec.ts', text: own }])).toHaveLength(1);
    expect(profileOffenders([{ name: 'b.spec.ts', text: env }])).toHaveLength(1);
    expect(profileOffenders([{ name: 'c.spec.ts', text: launch }]).length).toBeGreaterThan(0);
    expect(profileOffenders([{ name: 'd.spec.ts', text: plain }])).toHaveLength(1);
    expect(profileOffenders([{ name: 'e.spec.ts', text: fine }])).toEqual([]);
  });
});
