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
const REGISTRY_NS = 'https://mosaiq.maxthecoder.online/xml/model-registry/v1';

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

describe('model-registry documents', () => {
  it('keeps one variable, one centroid and one label a list, and a lone version an object', () => {
    const one = parseXml(
      `<ModelVersion xmlns="${REGISTRY_NS}"><parameters><features>R</features></parameters><frozen_scoring><centroids><cluster_index>0</cluster_index></centroids></frozen_scoring><reference_distribution><labels><label_code>A</label_code></labels></reference_distribution></ModelVersion>`,
    ) as { ModelVersion: Record<string, Record<string, unknown>> };
    expect(one.ModelVersion.parameters?.features).toEqual(['R']);
    expect(Array.isArray(one.ModelVersion.frozen_scoring?.centroids)).toBe(true);
    expect(Array.isArray(one.ModelVersion.reference_distribution?.labels)).toBe(true);
    const page = parseXml(
      `<ModelVersions xmlns="${REGISTRY_NS}"><ModelVersion><parameters><features>F</features></parameters></ModelVersion></ModelVersions>`,
    ) as { ModelVersions: { ModelVersion: { parameters: { features: unknown } }[] } };
    expect(page.ModelVersions.ModelVersion[0]?.parameters.features).toEqual(['F']);
    const training = parseXml(
      `<ModelTrainings xmlns="${REGISTRY_NS}"><ModelTraining><parameters><features>M</features></parameters></ModelTraining></ModelTrainings>`,
    ) as { ModelTrainings: { ModelTraining: { parameters: { features: unknown } }[] } };
    expect(training.ModelTrainings.ModelTraining[0]?.parameters.features).toEqual(['M']);
  });
});

describe('buildXml', () => {
  it('writes a nested record as a child element and a list as repeated elements, in order', async () => {
    const xml = buildXml('ModelTrainingRequest', REGISTRY_NS, {
      version_name: 'v',
      method: 'KMEANS',
      window_days: '90',
      parameters: {
        k: '3',
        max_iterations: '50',
        tolerance: '0.001',
        features: ['R', 'M'],
        normalization: 'NONE',
      },
    });
    expect(xml).toContain(
      '<parameters><k>3</k><max_iterations>50</max_iterations><tolerance>0.001</tolerance><features>R</features><features>M</features><normalization>NONE</normalization></parameters>',
    );
    expect((await new XsdValidator(bundle).validate(xml, REGISTRY_NS)).valid).toBe(true);
  });

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
