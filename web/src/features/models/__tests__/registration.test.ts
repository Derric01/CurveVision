import { describe, expect, it } from 'vitest';
import type { DiscoveredModel } from '@/api/types';
import {
  canRegisterModels,
  connectionOf,
  describeDiscovery,
  emptyDraft,
  fillFrom,
  normaliseEndpoint,
  parseLabels,
  problemsWith,
  registrationOf,
  type RegistrationDraft,
} from '../registration';

const DETECTOR: DiscoveredModel = {
  id: 'yolo-v8n',
  name: 'YOLOv8n',
  kind: 'detector',
  labels: ['car', 'truck'],
  shape_types: ['rectangle'],
  open_vocabulary: false,
  description: null,
};

function draft(overrides: Partial<RegistrationDraft> = {}): RegistrationDraft {
  return { ...emptyDraft(), endpoint: 'http://gpu-box:8000/infer', ...overrides };
}

describe('what is registered', () => {
  it('is what discovery found, under the id the endpoint knows the model by', () => {
    const filled = fillFrom(draft({ apiKey: 'k' }), DETECTOR);

    expect(registrationOf(filled)).toEqual({
      slug: 'yolov8n',
      name: 'YOLOv8n',
      provider: 'http',
      kind: 'detector',
      config: { endpoint: 'http://gpu-box:8000/infer', api_key: 'k', model: 'yolo-v8n' },
      output_labels: ['car', 'truck'],
      open_vocabulary: false,
    });
  });

  it('sends no key and no model name when there is none', () => {
    const typed = draft({ name: 'Plates', labels: 'plate' });

    expect(registrationOf(typed).config).toEqual({ endpoint: 'http://gpu-box:8000/infer' });
  });

  it('trims what was typed', () => {
    const typed = draft({
      endpoint: '  http://gpu-box:8000/infer ',
      apiKey: '  k  ',
      name: ' Plates ',
      labels: ' plate , plate,, sign ',
    });

    const registration = registrationOf(typed);
    expect(registration.config).toEqual({ endpoint: 'http://gpu-box:8000/infer', api_key: 'k' });
    expect(registration.name).toBe('Plates');
    expect(registration.output_labels).toEqual(['plate', 'sign']);
  });

  it('keeps what a person changed after discovery filled the form in', () => {
    const edited = { ...fillFrom(draft(), DETECTOR), name: 'Vehicles', labels: 'car' };

    expect(registrationOf(edited)).toMatchObject({
      slug: 'vehicles',
      output_labels: ['car'],
      config: { model: 'yolo-v8n' },
    });
  });
});

describe('what stops a registration', () => {
  it('is nothing, for a filled-in model', () => {
    expect(problemsWith(fillFrom(draft(), DETECTOR))).toEqual([]);
  });

  it('is an endpoint that is not an http URL', () => {
    for (const endpoint of ['', 'gpu-box:8000', 'ftp://gpu-box/infer', 'not a url']) {
      expect(problemsWith(fillFrom(draft({ endpoint }), DETECTOR))).toEqual([
        'The endpoint must be an http:// or https:// URL.',
      ]);
    }
    expect(normaliseEndpoint('https://models.example.com/v1/infer')).toBe(
      'https://models.example.com/v1/infer',
    );
  });

  it('is a fixed-head model with no labels, which could be run and never kept', () => {
    expect(problemsWith(draft({ name: 'Plates' }))).toEqual([
      'Name the labels this model returns. Without them nothing it finds can be kept.',
    ]);
  });

  it('is not an open-vocabulary model with no labels, which is told what to find', () => {
    expect(problemsWith(draft({ name: 'World', openVocabulary: true }))).toEqual([]);
  });

  it('is a name with nothing to make a slug from', () => {
    expect(problemsWith(draft({ name: '', labels: 'car' }))).toEqual(['Give the model a name.']);
    expect(problemsWith(draft({ name: '!!', labels: 'car' }))).toEqual([
      'The name needs at least two letters or digits.',
    ]);
  });
});

describe('asking the endpoint', () => {
  it('sends the key only when one was typed', () => {
    expect(connectionOf(draft({ apiKey: '   ' }))).toEqual({
      endpoint: 'http://gpu-box:8000/infer',
    });
    expect(connectionOf(draft({ apiKey: 'k' }))).toEqual({
      endpoint: 'http://gpu-box:8000/infer',
      api_key: 'k',
    });
  });

  it('says what it found, including nothing', () => {
    expect(describeDiscovery([])).toMatch(/does not list its models/);
    expect(describeDiscovery([DETECTOR])).toBe('Connected. This endpoint serves one model.');
    expect(describeDiscovery([DETECTOR, { ...DETECTOR, id: 'other' }])).toMatch(
      /serves 2 models — pick/,
    );
  });
});

describe('who is offered the form', () => {
  it('is whoever the server lets register a model', () => {
    expect(canRegisterModels('maintainer')).toBe(true);
    expect(canRegisterModels('owner')).toBe(true);
    expect(canRegisterModels('annotator')).toBe(false);
    expect(canRegisterModels('viewer')).toBe(false);
    // An older server sends no role: offer the form, and let its 403 answer.
    expect(canRegisterModels(undefined)).toBe(true);
  });
});

describe('labels', () => {
  it('are split on commas, trimmed and de-duplicated in order', () => {
    expect(parseLabels('car, truck ,car,, bus')).toEqual(['car', 'truck', 'bus']);
  });
});
