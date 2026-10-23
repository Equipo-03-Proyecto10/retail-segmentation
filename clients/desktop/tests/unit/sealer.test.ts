import { describe, expect, it } from 'vitest';
import { MemorySealer } from '../../src/main/sealer';

describe('MemorySealer', () => {
  it('round-trips without the plain text in the sealed bytes', () => {
    const sealer = new MemorySealer();
    const sealed = sealer.seal('token-SECRET-1');
    expect(sealed.includes('token-SECRET-1')).toBe(false);
    expect(sealer.open(sealed)).toBe('token-SECRET-1');
  });

  it('seals the same text differently each time', () => {
    const sealer = new MemorySealer();
    expect(sealer.seal('t').equals(sealer.seal('t'))).toBe(false);
  });

  it('refuses tampered bytes and another process key', () => {
    const sealer = new MemorySealer();
    const sealed = sealer.seal('token');
    const tampered = Buffer.from(sealed);
    tampered[tampered.length - 1] ^= 1;
    expect(() => sealer.open(tampered)).toThrow();
    expect(() => new MemorySealer().open(sealed)).toThrow();
  });
});
