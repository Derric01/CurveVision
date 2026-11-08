import { describe, expect, it } from 'vitest';
import type { AnnotationDocument, ApiShape, ApiTag, ApiTrack } from '@/api/types';
import { isUnreviewed } from '@/canvas/types';
import {
  NOTHING_TO_REVIEW,
  describeDecision,
  describeConfidence,
  describePending,
  nextSuggestion,
  pendingReview,
  selectedForReview,
  type SuggestionPlace,
} from '../suggestions';

function shape(overrides: Partial<ApiShape> = {}): ApiShape {
  return {
    id: 's1',
    client_id: null,
    label_id: 'l1',
    frame: 0,
    shape_type: 'rectangle',
    points: [0, 0, 10, 10],
    rotation: 0,
    occluded: false,
    outside: false,
    z_order: 0,
    group: null,
    source: 'model',
    confidence: 0.8,
    attributes: {},
    mask: null,
    elements: [],
    ...overrides,
  };
}

function track(overrides: Partial<ApiTrack> = {}): ApiTrack {
  return {
    id: 't1',
    client_id: null,
    label_id: 'l1',
    shape_type: 'rectangle',
    group: null,
    object_id: null,
    source: 'model',
    confidence: 0.6,
    attributes: {},
    shapes: [],
    ...overrides,
  };
}

function tag(overrides: Partial<ApiTag> = {}): ApiTag {
  return {
    id: 'g1',
    client_id: null,
    label_id: 'l1',
    frame: 0,
    source: 'model',
    confidence: 0.9,
    attributes: {},
    ...overrides,
  };
}

function document(overrides: Partial<AnnotationDocument> = {}): AnnotationDocument {
  return {
    job_id: 'j1',
    annotation_version: 1,
    shapes: [],
    tracks: [],
    tags: [],
    ...overrides,
  };
}

describe('isUnreviewed', () => {
  it('is true for a model prediction nobody has ruled on', () => {
    expect(isUnreviewed({ source: 'model', confidence: 0.8 })).toBe(true);
  });

  // The whole reason the predicate is a pair: accepting keeps `source = "model"` for
  // provenance and only clears the confidence, so a source check alone never changes.
  it('is false once accepted, which clears the confidence but keeps the source', () => {
    expect(isUnreviewed({ source: 'model', confidence: null })).toBe(false);
    expect(isUnreviewed({ source: 'model' })).toBe(false);
  });

  it('is false for a suggestion a human has edited', () => {
    expect(isUnreviewed({ source: 'model_corrected', confidence: 0.8 })).toBe(false);
  });

  it('is false for work nobody proposed', () => {
    expect(isUnreviewed({ source: 'manual', confidence: null })).toBe(false);
    expect(isUnreviewed({ source: 'imported', confidence: 0.4 })).toBe(false);
    expect(isUnreviewed({ source: 'interpolated', confidence: 0.4 })).toBe(false);
  });

  // A model that reports no confidence at all would otherwise read as pre-accepted. The
  // server defaults it to 1.0, so this is about being explicit rather than lucky.
  it('treats a confidence of zero as a real one', () => {
    expect(isUnreviewed({ source: 'model', confidence: 0 })).toBe(true);
  });
});

describe('pendingReview', () => {
  it('has nothing to do without a document', () => {
    expect(pendingReview(undefined)).toEqual(NOTHING_TO_REVIEW);
  });

  it('collects shapes, tracks and tags separately, the way the endpoint takes them', () => {
    const review = pendingReview(
      document({ shapes: [shape()], tracks: [track()], tags: [tag()] }),
    );
    expect(review).toEqual({
      shapeIds: ['s1'],
      trackIds: ['t1'],
      tagIds: ['g1'],
      total: 3,
    });
  });

  it('leaves out everything already ruled on', () => {
    const review = pendingReview(
      document({
        shapes: [
          shape({ id: 'pending' }),
          shape({ id: 'accepted', confidence: null }),
          shape({ id: 'edited', source: 'model_corrected' }),
          shape({ id: 'drawn', source: 'manual', confidence: null }),
        ],
      }),
    );
    expect(review.shapeIds).toEqual(['pending']);
    expect(review.total).toBe(1);
  });

  it('is empty for a job nobody ran a model over', () => {
    const review = pendingReview(
      document({ shapes: [shape({ source: 'manual', confidence: null })] }),
    );
    expect(review.total).toBe(0);
  });
});

describe('describePending', () => {
  // The buttons act on the job, so the count has to say so: it is routinely larger than
  // what is on the frame in front of you.
  it('names the job rather than the frame', () => {
    expect(describePending(4)).toContain('in this job');
  });

  it('gets the singular right', () => {
    expect(describePending(1)).toBe('1 suggestion in this job is waiting for review.');
    expect(describePending(2)).toBe('2 suggestions in this job are waiting for review.');
  });

  it('has something to say about nothing', () => {
    expect(describePending(0)).toBe('Nothing is waiting for review.');
  });
});

describe('describeDecision', () => {
  it('adds the three counts the server reports', () => {
    expect(describeDecision({ shapes: 2, tracks: 1, tags: 0 }, true)).toContain('3 suggestions');
  });

  // "Accepted" must not read as "created something new": nothing moved, the provenance did.
  it('says what accepting left behind', () => {
    expect(describeDecision({ shapes: 1, tracks: 0, tags: 0 }, true)).toBe(
      'Kept 1 suggestion. They are ordinary annotations now.',
    );
  });

  it('says plainly that rejecting deletes', () => {
    expect(describeDecision({ shapes: 5, tracks: 0, tags: 0 }, false)).toBe('Deleted 5 suggestions.');
  });

  it('handles a server that reported nothing', () => {
    expect(describeDecision(undefined, true)).toBe('Nothing was accepted.');
    expect(describeDecision({ shapes: 0, tracks: 0, tags: 0 }, false)).toBe('Nothing was rejected.');
  });
});

describe('deciding the selected ones', () => {
  const document: AnnotationDocument = {
    job_id: 'j',
    annotation_version: 3,
    shapes: [
      shape({ id: 'waiting' }),
      shape({ id: 'accepted', confidence: null }),
      shape({ id: 'manual', source: 'manual', confidence: null }),
    ],
    tracks: [track({ id: 'tracked', shapes: [] })],
    tags: [tag()],
  } as AnnotationDocument;

  it('takes only the selected suggestions still waiting, never tags', () => {
    expect(selectedForReview(document, ['waiting', 'accepted', 'manual', 'tracked', 'g1'])).toEqual({
      shapeIds: ['waiting'],
      trackIds: ['tracked'],
      tagIds: [],
      total: 2,
    });
    expect(selectedForReview(document, [])).toEqual(NOTHING_TO_REVIEW);
  });
});

describe('the next suggestion', () => {
  const document = {
    job_id: 'j',
    annotation_version: 1,
    shapes: [
      shape({ id: 'b', frame: 2 }),
      shape({ id: 'a', frame: 2 }),
      shape({ id: 'late', frame: 9 }),
      shape({ id: 'done', frame: 5, confidence: null }),
    ],
    tracks: [
      track({
        id: 'trk',
        shapes: [{ frame: 6 }, { frame: 4 }] as ApiTrack['shapes'],
      }),
    ],
    tags: [],
  } as unknown as AnnotationDocument;

  it('is the first waiting on or after the frame on screen', () => {
    expect(nextSuggestion(document, { frame: 0 })).toMatchObject({ frame: 2, id: 'a' });
    expect(nextSuggestion(document, { frame: 3 })).toMatchObject({ frame: 4, id: 'trk' });
  });

  it('moves on from the one selected, and wraps round after the last', () => {
    expect(nextSuggestion(document, { frame: 2, id: 'a' })).toMatchObject({ frame: 2, id: 'b' });
    expect(nextSuggestion(document, { frame: 9, id: 'late' })).toMatchObject({ frame: 2, id: 'a' });
    expect(nextSuggestion(document, { frame: 10 })).toMatchObject({ frame: 2, id: 'a' });
  });

  it('is nothing when nothing is waiting', () => {
    expect(nextSuggestion({ ...document, shapes: [], tracks: [] }, { frame: 0 })).toBeNull();
  });
});

describe('least confident first', () => {
  const document = {
    job_id: 'j',
    annotation_version: 1,
    shapes: [
      shape({ id: 'sure', frame: 1, confidence: 0.95 }),
      shape({ id: 'unsure', frame: 8, confidence: 0.31 }),
      shape({ id: 'middling', frame: 3, confidence: 0.6 }),
      shape({ id: 'decided', frame: 0, confidence: null }),
    ],
    tracks: [track({ id: 'trk', confidence: 0.45, shapes: [{ frame: 5 }] as ApiTrack['shapes'] })],
    tags: [],
  } as unknown as AnnotationDocument;

  it('starts with the least sure, wherever it is in the job', () => {
    expect(nextSuggestion(document, { frame: 0 }, 'confidence')).toMatchObject({
      id: 'unsure',
      frame: 8,
      confidence: 0.31,
    });
  });

  it('then goes up in confidence, and wraps round', () => {
    const walk: string[] = [];
    let id: string | null = null;
    for (let step = 0; step < 5; step += 1) {
      const next: SuggestionPlace = nextSuggestion(document, { frame: 0, id }, 'confidence')!;
      walk.push(next.id);
      id = next.id;
    }
    expect(walk).toEqual(['unsure', 'trk', 'middling', 'sure', 'unsure']);
  });

  it('reads as a person would say it', () => {
    expect(describeConfidence(0.617)).toBe('62% sure');
    expect(describeConfidence(null)).toBeNull();
  });
});
