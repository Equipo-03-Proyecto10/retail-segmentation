import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

/** The local stack's gateway (F13-08). */
export const DEFAULT_BASE_URL = 'http://127.0.0.1:8080';
export const SETTINGS_FILE = 'settings.json';

export interface Settings {
  baseUrl: string;
}

/**
 * Reads <dir>/settings.json. A missing file means the default; a present but
 * invalid one is an error the operator must fix, never silently ignored.
 */
export function loadSettings(dir: string): Settings {
  mkdirSync(dir, { recursive: true });
  let text: string;
  try {
    text = readFileSync(join(dir, SETTINGS_FILE), 'utf8');
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === 'ENOENT') return { baseUrl: DEFAULT_BASE_URL };
    throw e;
  }
  const parsed = JSON.parse(text) as { baseUrl?: unknown };
  const raw = parsed.baseUrl ?? DEFAULT_BASE_URL;
  if (typeof raw !== 'string') throw new Error('settings.json: baseUrl must be a string');
  return { baseUrl: normalizeBaseUrl(raw) };
}

export function normalizeBaseUrl(raw: string): string {
  const url = new URL(raw);
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error('settings.json: baseUrl must be http or https');
  }
  return `${url.origin}${url.pathname.replace(/\/+$/, '')}`;
}
