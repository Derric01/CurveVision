import { describe, expect, it } from 'vitest';
import type { AnnotationDocument, ApiTag } from '@/api/types';
import type { LabelStyle } from '@/canvas/types';
import { tagsOn, taggableLabels } from '../frameTags';

const labels: LabelStyle[] = [
  { id: 'night', name: 'night', color: '#111111', visible: true, locked: false },
  { id: 'blur', name: 'blurred', color: '#222222', visible: true, locked: false },
  { id: 'person', name: 'person', color: '#333333', visible: true, locked: false },
  {
    id: 'knee',
    name: 'knee',
    color: '#444444',
    visible: true,
    locked: false,
    parentId: 'person',
  },
];

function tag(overrides: Partial<ApiTag>): ApiTag {
  return {
    id: 't',
    client_id: null,
    label_id: 'night',
    frame: 0,
    source: 'manual',
    confidence: null,
    attributes: {},
    ...overrides,
  };
}

function document(tags: ApiTag[]): AnnotationDocument {
  return { job_id: 'j', annotation_version: 1, shapes: [], tracks: [], tags };
}

describe('tagsOn', () => {
  it('lists only the frame’s own tags, in label order, named and coloured', () => {
    const doc = document([
      tag({ id: 'a', label_id: 'blur', frame: 2 }),
      tag({ id: 'b', label_id: 'night', frame: 2 }),
      tag({ id: 'c', label_id: 'night', frame: 3 }),
      tag({ id: 'd', label_id: 'night', frame: null }),
    ]);
    expect(tagsOn(doc, 2, labels)).toEqual([
      { id: 'b', labelId: 'night', name: 'night', color: '#111111', suggested: false },
      { id: 'a', labelId: 'blur', name: 'blurred', color: '#222222', suggested: false },
    ]);
  });

  it('says when a tag is still a model’s guess', () => {
    const doc = document([tag({ source: 'model', confidence: 0.7 })]);
    expect(tagsOn(doc, 0, labels)[0]?.suggested).toBe(true);
    const accepted = document([tag({ source: 'model', confidence: null })]);
    expect(tagsOn(accepted, 0, labels)[0]?.suggested).toBe(false);
  });

  it('is empty before the document arrives, and survives a label it does not know', () => {
    expect(tagsOn(undefined, 0, labels)).toEqual([]);
    const [unknown] = tagsOn(document([tag({ label_id: 'gone' })]), 0, labels);
    expect(unknown?.name).toBe('unknown label');
  });
});

describe('taggableLabels', () => {
  it('offers the project’s own labels the frame does not carry yet, never a joint', () => {
    const present = tagsOn(document([tag({ label_id: 'night' })]), 0, labels);
    expect(taggableLabels(labels, present).map((label) => label.id)).toEqual(['blur', 'person']);
  });
});
