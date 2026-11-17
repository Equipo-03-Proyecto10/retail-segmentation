import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

const WINDOW_TESTS = join(__dirname, '..', 'window');
const PROFILE_PRIMITIVE = /\b(?:mkdtemp(?:Sync)?|tmpdir)\s*\(|MOSAIQ_DESKTOP_CONFIG_DIR/;

function windowSpecs(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return windowSpecs(path);
    return entry.name.endsWith('.spec.ts') ? [path] : [];
  });
}

describe('window test profile ownership', () => {
  it('keeps temporary profile creation in the shared helper', () => {
    const offenders = windowSpecs(WINDOW_TESTS)
      .filter((path) => PROFILE_PRIMITIVE.test(readFileSync(path, 'utf8')))
      .map((path) => relative(WINDOW_TESTS, path));

    expect(offenders, `profile creation outside electron-test.ts: ${offenders.join(', ')}`).toEqual([]);
  });
});
