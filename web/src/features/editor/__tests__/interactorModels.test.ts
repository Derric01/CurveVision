import { describe, expect, it } from 'vitest';
import type { ModelRegistration } from '@/api/types';
import { interactorModels, outlineFrom } from '../interactorModels';

function model(overrides: Partial<ModelRegistration>): ModelRegistration {
  return {
    id: 'm',
    organization_id: 'o',
    slug: 'sam',
    name: 'SAM',
    description: null,
    provider: 'http',
    kind: 'interactor',
    output_labels: [],
    is_active: true,
    ...overrides,
  } as ModelRegistration;
}

describe('which models answer clicks', () => {
  it('is the active interactors', () => {
    const models = [
      model({ id: 'a' }),
      model({ id: 'b', is_active: false }),
      model({ id: 'c', kind: 'detector' }),
    ];
    expect(interactorModels(models).map((m) => m.id)).toEqual(['a']);
    expect(interactorModels(undefined)).toEqual([]);
  });
});

describe('the outline in an answer', () => {
  const square = [10, 10, 60, 10, 60, 60, 10, 60];

  it('is the most confident polygon on the frame asked about', () => {
    expect(
      outlineFrom(
        [
          { frame: 3, type: 'polygon', points: [0, 0, 1, 0, 1, 1], confidence: 0.4 },
          { frame: 3, type: 'polygon', points: square, confidence: 0.9 },
          { frame: 4, type: 'polygon', points: [5, 5, 6, 5, 6, 6], confidence: 1 },
        ],
        3,
      ),
    ).toEqual({ points: square });
  });

  it('is nothing when the model found nothing', () => {
    expect(outlineFrom([], 3)).toEqual({ points: [] });
  });

  it('says so when the model answered with something else', () => {
    expect(outlineFrom([{ frame: 3, type: 'rectangle', points: [0, 0, 5, 5] }], 3)).toEqual({
      error: 'The model answered with a rectangle, not an outline',
    });
  });
});
