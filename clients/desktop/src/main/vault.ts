import { chmodSync, existsSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { safeStorage } from 'electron';
import type { TokenVault, Tokens } from './backoffice';

/**
 * Tokens rest encrypted with safeStorage. When the platform offers no
 * encryption, they are kept in memory only and never written to disk.
 */
export class SafeStorageVault implements TokenVault {
  private readonly file: string;

  constructor(dir: string) {
    this.file = join(dir, 'session.bin');
  }

  load(): Tokens | null {
    if (!safeStorage.isEncryptionAvailable() || !existsSync(this.file)) return null;
    try {
      const parsed = JSON.parse(safeStorage.decryptString(readFileSync(this.file))) as Tokens;
      return typeof parsed.access === 'string' && typeof parsed.refresh === 'string' ? parsed : null;
    } catch {
      this.clear();
      return null;
    }
  }

  save(tokens: Tokens): void {
    if (!safeStorage.isEncryptionAvailable()) return;
    writeFileSync(this.file, safeStorage.encryptString(JSON.stringify(tokens)), { mode: 0o600 });
    chmodSync(this.file, 0o600);
  }

  clear(): void {
    rmSync(this.file, { force: true });
  }
}
