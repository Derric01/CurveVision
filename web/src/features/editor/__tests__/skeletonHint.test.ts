import { describe, expect, it } from 'vitest';
import type { LabelStyle } from '@/canvas/types';
import { skeletonSchema } from '@/canvas/skeleton';
import { SKELETON_KEYS, skeletonHint, skeletonLabels } from '../skeletonHint';

const ARM = skeletonSchema({
  id: 'arm',
  name: 'arm',
  children: [
    { id: 'j-shoulder', name: 'shoulder' },
    { id: 'j-elbow', name: 'elbow' },
  ],
});

const HAND = skeletonSchema({
  id: 'hand',
  name: 'hand',
  children: [{ id: 'h-thumb', name: 'thumb' }],
});

function style(id: string, name: string, skeleton?: LabelStyle['skeleton']): LabelStyle {
  return {
    id,
    name,
    color: '#ef4444',
    visible: true,
    locked: false,
    ...(skeleton ? { skeleton } : {}),
  };
}

const LABELS: LabelStyle[] = [
  style('car', 'car'),
  style('arm', 'arm', ARM ?? undefined),
  style('hand', 'hand', HAND ?? undefined),
];

describe('skeletonLabels', () => {
  it('keeps only the labels that declare joints, in project order', () => {
    expect(skeletonLabels(LABELS).map((label) => label.name)).toEqual(['arm', 'hand']);
  });

  it('is empty when nothing in the project has joints', () => {
    expect(skeletonLabels([style('car', 'car')])).toEqual([]);
  });
});

describe('skeletonHint', () => {
  // The dead end: every click does nothing and the canvas looks broken. The hint has to
  // say what is wrong *and* what to do, or picking the tool is a trap.
  it('explains that the project has no keypoints at all', () => {
    const hint = skeletonHint([style('car', 'car')], 'car', null);
    expect(hint).toContain('No label in this project declares keypoints');
    expect(hint).toContain('project schema');
  });

  it('names the labels that would work when none is chosen', () => {
    expect(skeletonHint(LABELS, null, null)).toBe('Choose a label with joints: arm, hand.');
  });

  it('names the alternatives when the chosen label has no joints', () => {
    expect(skeletonHint(LABELS, 'car', null)).toBe(
      'car declares no keypoints. Choose arm or hand instead.',
    );
  });

  it('says what to click first, and how many joints there are', () => {
    const hint = skeletonHint(LABELS, 'arm', null);
    expect(hint).toContain('Click shoulder to start the 2-joint arm');
    expect(hint).toContain(SKELETON_KEYS);
  });

  it('defers to the engine once a skeleton is in progress', () => {
    const hint = skeletonHint(LABELS, 'arm', 'elbow — joint 2 of 2');
    expect(hint).toBe(`elbow — joint 2 of 2 · ${SKELETON_KEYS}`);
  });

  it('always says which keys skip, finish and undo', () => {
    expect(SKELETON_KEYS).toContain('X skips');
    expect(SKELETON_KEYS).toContain('Enter finishes');
    expect(SKELETON_KEYS).toContain('Backspace undoes');
  });

  // An unknown id is what a stale selection looks like after the label list reloads.
  it('treats a label id that is not in the list as nothing chosen', () => {
    expect(skeletonHint(LABELS, 'deleted-label', null)).toBe(
      'Choose a label with joints: arm, hand.',
    );
  });
});
