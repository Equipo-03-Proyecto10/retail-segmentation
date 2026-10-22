import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';

const IV = 12;
const TAG = 16;

/**
 * AES-256-GCM under a key that lives only in this process. It keeps tokens out of memory as plain
 * strings when no OS keyring is available; it does not protect them from code running in this
 * process, which can read the key too.
 */
export class MemorySealer {
  private readonly key = randomBytes(32);

  seal(plain: string): Buffer {
    const iv = randomBytes(IV);
    const cipher = createCipheriv('aes-256-gcm', this.key, iv);
    const body = Buffer.concat([cipher.update(plain, 'utf8'), cipher.final()]);
    return Buffer.concat([iv, cipher.getAuthTag(), body]);
  }

  open(sealed: Buffer): string {
    const decipher = createDecipheriv('aes-256-gcm', this.key, sealed.subarray(0, IV));
    decipher.setAuthTag(sealed.subarray(IV, IV + TAG));
    return Buffer.concat([decipher.update(sealed.subarray(IV + TAG)), decipher.final()]).toString('utf8');
  }
}
