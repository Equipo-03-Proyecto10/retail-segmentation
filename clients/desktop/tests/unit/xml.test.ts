import { describe, expect, it } from 'vitest';
import { rootElement } from '../../src/main/xml';

describe('rootElement', () => {
  it('reads the root and its namespace after the prolog', () => {
    const xml = '<?xml version="1.0"?><!-- note --><a:Error xmlns:a="urn:x"/>';
    expect(rootElement(xml)).toEqual({ local: 'Error', namespace: 'urn:x' });
  });

  it('strips comments until none is left, so one cannot hide inside another', () => {
    // A single pass removes the inner `<!---->` and leaves `<!-- c -->` behind.
    const xml = '<!-<!---->- c --><Error xmlns="urn:x"/>';
    expect(rootElement(xml)).toEqual({ local: 'Error', namespace: 'urn:x' });
  });

  it('refuses a DTD', () => {
    expect(rootElement('<!DOCTYPE a><a/>')).toBeNull();
  });
});
