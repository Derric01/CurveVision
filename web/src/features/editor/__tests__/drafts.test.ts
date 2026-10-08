/**
 * What goes into the copy of unsaved work the browser keeps, and what of it comes back.
 *
 * The storage and the offer are driven in a browser by `scripts/verify_crash_recovery.py`,
 * across a closed page and across a relaunch of the desktop server.
 */

import { describe, expect, it } from 'vitest';
import type { ApiShape, ApiTrack } from '@/api/types';
import type { Annotation } from '@/canvas/types';
import {
  draftOf,
  isEmptyDraft,
  planRestore,
  readDraft,
  restoreSize,
  type Draft,
  type LocalObject,
  type Queue,
} from '../drafts';
import type { CanvasEdit } from '../keyframes';

function box(id: string, overrides: Partial<Annotation> = {}): Annotation {
  return {
    id,
    labelId: 'car',
    frame: 0,
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

function edit(trackId: string, frame: number): CanvasEdit {
  return {
    trackId,
    frame,
    labelId: 'car',
    points: [1, 1, 5, 5],
    rotation: 0,
    occluded: false,
    attributes: {},
  };
}

function queue(overrides: Partial<Queue> = {}): Queue {
  return {
    created: new Map(),
    updated: new Map(),
    deleted: new Set(),
    tracks: new Map(),
    deletedTracks: new Set(),
    ...overrides,
  };
}

function draft(overrides: Partial<Draft> = {}): Draft {
  return {
    format: 2,
    savedAt: 1,
    annotationVersion: 3,
    created: [],
    updated: [],
    deleted: [],
    deletedClientIds: [],
    tracks: [],
    deletedTracks: [],
    ...overrides,
  };
}

function shape(id: string, clientId: string | null = null): ApiShape {
  return { id, client_id: clientId } as ApiShape;
}

function track(id: string): ApiTrack {
  return { id } as ApiTrack;
}

describe('draftOf', () => {
  it('keeps what is in flight as well as what is queued behind it', () => {
    // The batch a request is carrying is unconfirmed until its answer arrives. The old copy
    // was rewritten from the queue alone, so an edit made during a save dropped from it
    // every object that save was carrying.
    const sending = queue({ created: new Map([['a', box('a', { pending: true })]]) });
    const queued = queue({ updated: new Map([['s1', box('s1')]]) });
    const copy = draftOf({
      queues: [sending, queued],
      held: [],
      local: new Map(),
      annotationVersion: 7,
      savedAt: 99,
    });
    expect(copy.created.map((a) => a.id)).toEqual(['a']);
    expect(copy.updated.map((a) => a.id)).toEqual(['s1']);
    expect(copy.annotationVersion).toBe(7);
  });

  it('keeps an object the server refused, which exists nowhere else', () => {
    const copy = draftOf({
      queues: [queue()],
      held: [{ created: box('refused', { pending: true }) }, { tracks: [edit('t1', 4)] }],
      local: new Map(),
      annotationVersion: 1,
      savedAt: 0,
    });
    expect(copy.created.map((a) => a.id)).toEqual(['refused']);
    expect(copy.tracks).toEqual([edit('t1', 4)]);
  });

  it('lets the newer queue win for one object', () => {
    const copy = draftOf({
      queues: [
        queue({ updated: new Map([['s1', box('s1', { points: [0, 0, 1, 1] })]]) }),
        queue({ updated: new Map([['s1', box('s1', { points: [0, 0, 9, 9] })]]) }),
      ],
      held: [],
      local: new Map(),
      annotationVersion: 1,
      savedAt: 0,
    });
    expect(copy.updated).toEqual([box('s1', { points: [0, 0, 9, 9] })]);
  });

  it('keeps a deletion under the server id, never a local one the server cannot parse', () => {
    const local = new Map<string, LocalObject>([
      ['local-1', { clientId: 'c1', saved: 'server-1' }],
      ['local-2', { clientId: 'c2', saved: null }],
    ]);
    const copy = draftOf({
      queues: [
        queue({ created: new Map([['local-2', box('local-2', { pending: true })]]) }),
        queue({ deleted: new Set(['server-9', 'local-1', 'local-2']) }),
      ],
      held: [],
      local,
      annotationVersion: 1,
      savedAt: 0,
    });
    expect(copy.deleted).toEqual(['server-9', 'server-1']);
    // Deleted while its first save was in flight: not kept to create, and its deletion
    // names the client id, since whether that save landed is unknown.
    expect(copy.created).toEqual([]);
    expect(copy.deletedClientIds).toEqual(['c2']);
  });

  it('is empty when nothing is unsaved', () => {
    const copy = draftOf({
      queues: [queue()],
      held: [],
      local: new Map(),
      annotationVersion: 1,
      savedAt: 0,
    });
    expect(isEmptyDraft(copy)).toBe(true);
  });
});

describe('readDraft', () => {
  it('reads back what draftOf writes', () => {
    const copy = draft({ created: [box('a')], tracks: [edit('t', 1)], deleted: ['s'] });
    expect(readDraft(structuredClone(copy))).toEqual(copy);
  });

  it('reads a copy from before the version was recorded as made against an unknown one', () => {
    const old = { savedAt: 5, created: [box('a')], updated: [], deleted: [], tracks: [] };
    const read = readDraft(old);
    expect(read?.annotationVersion).toBeNull();
    expect(read?.created).toEqual([box('a')]);
  });

  it('drops anything that is not the right shape rather than handing it to the canvas', () => {
    const damaged = {
      format: 2,
      savedAt: 1,
      annotationVersion: 2,
      created: [box('a'), { id: 'no-points', labelId: 'car', frame: 0 }, null, 'x'],
      updated: 'not a list',
      deleted: ['s1', 7],
      tracks: [edit('t', 1), { trackId: 't' }],
    };
    const read = readDraft(damaged);
    expect(read?.created).toEqual([box('a')]);
    expect(read?.updated).toEqual([]);
    expect(read?.deleted).toEqual(['s1']);
    expect(read?.tracks).toEqual([edit('t', 1)]);
  });

  it('is nothing for nothing', () => {
    expect(readDraft(undefined)).toBeNull();
    expect(readDraft('junk')).toBeNull();
    expect(readDraft(draft())).toBeNull();
  });
});

describe('planRestore', () => {
  it('puts back what the server does not have', () => {
    const copy = draft({
      created: [box('local-1', { clientId: 'c1', pending: true })],
      updated: [box('s1')],
      deleted: ['s2'],
      tracks: [edit('t1', 3)],
      deletedTracks: ['t2'],
    });
    const plan = planRestore(
      copy,
      { shapes: [shape('s1'), shape('s2')], tracks: [track('t1'), track('t2')] },
      new Set(),
    );
    expect(plan.created.map((a) => a.id)).toEqual(['local-1']);
    expect(plan.updated.map((a) => a.id)).toEqual(['s1']);
    expect(plan.deleted).toEqual(['s2']);
    expect(plan.tracks).toEqual([edit('t1', 3)]);
    expect(plan.deletedTracks).toEqual(['t2']);
    expect(restoreSize(plan)).toBe(5);
  });

  it('skips a drawn object whose save did land, recognised by its client id', () => {
    // A crash between the server storing it and the browser hearing so. Sent again it would
    // be a second copy — the server holds one client id per job, so in fact a 500.
    const copy = draft({ created: [box('local-1', { clientId: 'c1', pending: true })] });
    const plan = planRestore(copy, { shapes: [shape('s9', 'c1')], tracks: [] }, new Set());
    expect(plan.created).toEqual([]);
  });

  it('deletes an object deleted during its first save, if that save landed', () => {
    const copy = draft({ deletedClientIds: ['c1', 'c2'] });
    const plan = planRestore(copy, { shapes: [shape('s9', 'c1')], tracks: [] }, new Set());
    expect(plan.deleted).toEqual(['s9']);
  });

  it('drops what refers to something the server no longer has', () => {
    const copy = draft({
      updated: [box('gone')],
      deleted: ['gone-too', 'local-7'],
      tracks: [edit('t-gone', 1)],
      deletedTracks: ['t-gone'],
    });
    const plan = planRestore(copy, { shapes: [], tracks: [] }, new Set());
    expect(restoreSize(plan)).toBe(0);
  });

  it('leaves alone anything this session has changed since, whose copy is newer', () => {
    const copy = draft({
      created: [box('local-1', { clientId: 'c1', pending: true })],
      updated: [box('s1'), box('s2')],
      deleted: ['s3'],
      tracks: [edit('t1', 3), edit('t1', 4), edit('t2', 1)],
    });
    const plan = planRestore(
      copy,
      {
        shapes: [shape('s1'), shape('s2'), shape('s3')],
        tracks: [track('t1'), track('t2')],
      },
      // `t1@3` edited in this session, `t2` deleted in it.
      new Set(['local-1', 's1', 's3', 't1@3', 't2']),
    );
    expect(plan.created).toEqual([]);
    expect(plan.updated.map((a) => a.id)).toEqual(['s2']);
    expect(plan.deleted).toEqual([]);
    expect(plan.tracks).toEqual([edit('t1', 4)]);
  });
});
