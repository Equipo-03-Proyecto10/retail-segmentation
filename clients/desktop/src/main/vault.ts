import { chmodSync, existsSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { safeStorage } from 'electron';
import type { Protection, SealedTokens, TokenVault } from './backoffice';
import { MemorySealer } from './sealer';

/**
 * With an OS keyring, tokens are sealed with safeStorage, in memory and at rest. Without one
 * (including Linux's basic_text backend, whose key is built in), they are sealed under a
 * per-process key, kept in memory only and never written to disk.
 */
export class SafeStorageVault implements TokenVault {
  readonly protection: Protection;
  private readonly file: string;
  private readonly memory = new MemorySealer();

  constructor(dir: string) {
    this.file = join(dir, 'session.bin');
    this.protection = keyringAvailable() ? 'os_keyring' : 'memory_only';
    // A session file is never trusted without a keyring to open it.
    if (this.protection === 'memory_only') this.clear();
  }

  seal(plain: string): Buffer {
    return this.protection === 'os_keyring' ? safeStorage.encryptString(plain) : this.memory.seal(plain);
  }

  open(sealed: Buffer): string {
    return this.protection === 'os_keyring' ? safeStorage.decryptString(sealed) : this.memory.open(sealed);
  }

  load(): SealedTokens | null {
    if (this.protection !== 'os_keyring' || !existsSync(this.file)) return null;
    try {
      const parsed = JSON.parse(readFileSync(this.file, 'utf8')) as Record<string, unknown>;
      const { access, refresh, expiresAt } = parsed;
      if (typeof access !== 'string' || typeof refresh !== 'string' || typeof expiresAt !== 'number')
        throw new Error('malformed session file');
      const tokens = {
        access: Buffer.from(access, 'base64'),
        refresh: Buffer.from(refresh, 'base64'),
        expiresAt,
      };
      this.open(tokens.access); // fails here, not mid-call, when the keyring cannot open it
      return tokens;
    } catch {
      this.clear();
      return null;
    }
  }

  save(tokens: SealedTokens): void {
    if (this.protection !== 'os_keyring') return;
    const body = JSON.stringify({
      access: tokens.access.toString('base64'),
      refresh: tokens.refresh.toString('base64'),
      expiresAt: tokens.expiresAt,
    });
    writeFileSync(this.file, body, { mode: 0o600 });
    chmodSync(this.file, 0o600);
  }

  clear(): void {
    rmSync(this.file, { force: true });
  }
}

function keyringAvailable(): boolean {
  if (!safeStorage.isEncryptionAvailable()) return false;
  if (process.platform !== 'linux') return true;
  const backend = safeStorage.getSelectedStorageBackend();
  return backend !== 'basic_text' && backend !== 'unknown';
}
