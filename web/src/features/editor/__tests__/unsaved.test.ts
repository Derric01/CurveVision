/**
 * Laying what autosave holds back over a frame the server has just been reloaded from.
 *
 * Without it an object the server refused — which is on no server — vanished with the
 * reload that followed the rest of its batch being saved, and could not be selected to fix.
 */

import { describe, expect, it } from 'vitest';
import type { Annotation } from '@/canvas/types';
import { withUnsaved, type UnsavedFrame } from '../adapters';
import type { CanvasEdit } from '../keyframes';

function box(id: string, overrides: Partial<Annotation> = {}): Annotation {
  return {
    id,
    labelId: 'car',
    frame: 3,
    shapeType: 'rectangle',
    points: [0, 0, 10, 10],
    rotation: 0,
    occluded: false,
    outside: false,
    zOrder: 0,
    source: 'manual',
    attributes: {},
    ...overrides,
  };
}

function unsaved(overrides: Partial<UnsavedFrame> = {}): UnsavedFrame {
  return { annotations: [], tracks: [], deletedIds: new Set(), ...overrides };
}

describe('withUnsaved', () => {
  it('is the server frame unchanged when nothing is held', () => {
    const onServer = [box('s1'), box('t1', { trackId: 't1' })];
    expect(withUnsaved(onServer, unsaved())).toEqual(onServer);
  });

  it('adds an object the server has never had, as it was drawn', () => {
    const drawn = box('client-1', { pending: true, attributes: { plate: undefined } });
    expect(withUnsaved([box('s1')], unsaved({ annotations: [drawn] }))).toEqual([
      box('s1'),
      drawn,
    ]);
  });

  it("shows an object's unsaved change in place of the server's copy", () => {
    const moved = box('s1', { points: [5, 5, 15, 15] });
    expect(withUnsaved([box('s1'), box('s2')], unsaved({ annotations: [moved] }))).toEqual([
      moved,
      box('s2'),
    ]);
  });

  it('does not draw a new object twice when its save landed in what the server returned', () => {
    // A save in flight when the frame was read can have committed before the read did: the
    // server's copy and the local one would both be drawn.
    const drawn = box('local-1', { clientId: 'c1', pending: true });
    const stored = box('s9', { clientId: 'c1' });
    expect(withUnsaved([stored], unsaved({ annotations: [drawn] }))).toEqual([stored]);
  });

  it('does not draw back a change to something the server no longer has', () => {
    // Deleted elsewhere; drawing the local copy would be a ghost nobody could save.
    expect(withUnsaved([], unsaved({ annotations: [box('gone')] }))).toEqual([]);
  });

  it('leaves out what is deleted and not yet sent', () => {
    expect(
      withUnsaved([box('s1'), box('t1', { trackId: 't1' })], unsaved({ deletedIds: new Set(['s1', 't1']) })),
    ).toEqual([]);
  });

  it("lays a tracked object's unsaved edit on this frame over where the track puts it", () => {
    const edit: CanvasEdit = {
      trackId: 't1',
      frame: 3,
      labelId: 'truck',
      points: [1, 2, 3, 4],
      rotation: 15,
      occluded: true,
      attributes: { parked: true },
    };
    const [shown] = withUnsaved([box('t1', { trackId: 't1' })], unsaved({ tracks: [edit] }));
    expect(shown).toEqual(
      box('t1', {
        trackId: 't1',
        labelId: 'truck',
        points: [1, 2, 3, 4],
        rotation: 15,
        occluded: true,
        attributes: { parked: true },
      }),
    );
  });
});
