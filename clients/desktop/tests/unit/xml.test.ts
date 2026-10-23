import { describe, expect, it } from 'vitest';
import { buildXml, parseXml, rootElement, XsdValidator } from '../../src/main/xml';
import { bundle } from '../support/fake';

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

const CATEGORIES_NS = 'https://mosaiq.maxthecoder.online/xml/categories/v1';
const PRODUCTS_NS = 'https://mosaiq.maxthecoder.online/xml/products/v1';

describe('parseXml', () => {
  it('keeps a one-item page as a list, and a single resource as an object', () => {
    const page = parseXml(
      `<Products xmlns="${PRODUCTS_NS}"><Product><product_id>1</product_id></Product></Products>`,
    );
    expect(Array.isArray((page.Products as { Product: unknown }).Product)).toBe(true);
    const one = parseXml(`<Product xmlns="${PRODUCTS_NS}"><product_id>1</product_id></Product>`);
    expect(Array.isArray(one.Product)).toBe(false);
  });
});

describe('buildXml', () => {
  it('writes a null field as xsi:nil, and the body validates', async () => {
    const xml = buildXml('UpdateCategoryRequest', CATEGORIES_NS, { parent_category_id: null });
    expect(xml).toContain('<parent_category_id xsi:nil="true"/>');
    expect(xml).toContain('xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"');
    expect((await new XsdValidator(bundle).validate(xml, CATEGORIES_NS)).valid).toBe(true);
  });

  it('declares no xsi namespace when nothing is nil', () => {
    const xml = buildXml('UpdateCategoryRequest', CATEGORIES_NS, { name: 'Cheese' });
    expect(xml).not.toContain('xmlns:xsi');
    expect(xml).toContain('<name>Cheese</name>');
  });
});
