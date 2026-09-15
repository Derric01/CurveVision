/**
 * The skeleton tool as a state machine.
 *
 * `skeleton.test.ts` covers the ordering rules; this covers the interaction on top of them:
 * what a click, a skip, an undo and an Enter each do, and — the part that matters most —
 * that what is committed always has one element per declared joint in declared order,
 * however the annotator got there.
 */

import { describe, expect, it } from 'vitest';
import { Scene } from '../scene';
import { SkeletonTool, createTool, TOOL_SHORTCUTS, type ToolContext } from '../tools';
import { skeletonSchema } from '../skeleton';
import { createViewport } from '../viewport';
import type { LabelStyle, PointerInput } from '../types';

const SCHEMA = skeletonSchema({
  id: 'arm',
  name: 'arm',
  children: [
    { id: 'j-shoulder', name: 'shoulder' },
    { id: 'j-elbow', name: 'elbow' },
    { id: 'j-wrist', name: 'wrist' },
  ],
  skeletonEdges: [
    [0, 1],
    [1, 2],
  ],
});

function style(id: string, name: string, skeleton?: LabelStyle['skeleton']): LabelStyle {
  return { id, name, color: '#ef4444', visible: true, locked: false, ...(skeleton ? { skeleton } : {}) };
}

function context(labelId: string | null = 'arm'): ToolContext {
  const scene = new Scene();
  scene.setLabels([style('arm', 'arm', SCHEMA ?? undefined), style('car', 'car')]);
  return {
    scene,
    viewport: () => ({ ...createViewport(800, 600), scale: 1 }),
    imageSize: () => ({ width: 1000, height: 800 }),
    activeLabelId: () => labelId,
    snapEnabled: () => false,
  };
}

function pointer(x: number, y: number, extra: Partial<PointerInput> = {}): PointerInput {
  return {
    image: { x, y },
    screen: { x, y },
    shiftKey: false,
    ctrlKey: false,
    altKey: false,
    button: 0,
    ...extra,
  };
}

describe('placing joints', () => {
  it('is reachable from the tool registry and its shortcut', () => {
    expect(createTool('skeleton')).toBeInstanceOf(SkeletonTool);
    expect(TOOL_SHORTCUTS.j).toBe('skeleton');
  });

  it('commits by itself when the last joint is placed, with no extra keystroke', () => {
    const tool = new SkeletonTool();
    const ctx = context();

    expect(tool.onPointerDown(pointer(10, 10), ctx).created).toBeUndefined();
    expect(tool.onPointerDown(pointer(20, 20), ctx).created).toBeUndefined();
    const result = tool.onPointerDown(pointer(30, 30), ctx);

    expect(result.created?.shapeType).toBe('skeleton');
    expect(result.created?.labelId).toBe('arm');
    expect(result.created?.elements?.map((element) => element.points)).toEqual([
      [10, 10],
      [20, 20],
      [30, 30],
    ]);
    expect(result.created?.points).toEqual([10, 10, 20, 20, 30, 30]);
    expect(result.draft).toBeNull();
  });

  it('reports which joint it wants next', () => {
    const tool = new SkeletonTool();
    const ctx = context();
    expect(tool.progress(ctx)).toBe('shoulder — joint 1 of 3');
    expect(tool.onPointerDown(pointer(10, 10), ctx).status).toBe('elbow — joint 2 of 3');
  });

  it('clamps a click outside the image rather than storing a point off it', () => {
    const tool = new SkeletonTool();
    const ctx = context();
    tool.onPointerDown(pointer(-40, 90_000), ctx);
    const result = tool.onKey('Enter', ctx);
    expect(result?.created?.elements?.[0]?.points).toEqual([0, 800]);
  });

  it('marks a joint occluded when Alt is held', () => {
    const tool = new SkeletonTool();
    const ctx = context();
    tool.onPointerDown(pointer(10, 10, { altKey: true }), ctx);
    tool.onPointerDown(pointer(20, 20), ctx);
    const result = tool.onPointerDown(pointer(30, 30), ctx);
    expect(result.created?.elements?.map((element) => element.occluded)).toEqual([
      true,
      false,
      false,
    ]);
  });

  it('does nothing at all when the active label declares no joints', () => {
    const tool = new SkeletonTool();
    const ctx = context('car');
    expect(tool.onPointerDown(pointer(10, 10), ctx)).toEqual({});
    expect(tool.decided).toBe(0);
  });

  it('does nothing when no label is active', () => {
    const tool = new SkeletonTool();
    expect(tool.onPointerDown(pointer(10, 10), context(null))).toEqual({});
  });

  it('draws nothing that trails the pointer', () => {
    const tool = new SkeletonTool();
    const ctx = context();
    tool.onPointerDown(pointer(10, 10), ctx);
    expect(tool.onPointerMove(pointer(50, 50), ctx)).toEqual({});
  });
});

describe('skipping a joint', () => {
  it('records the skip in its own slot and moves on', () => {
    const tool = new SkeletonTool();
    const ctx = context();
    tool.onPointerDown(pointer(10, 10), ctx);
    const skipped = tool.onKey('x', ctx);

    expect(skipped?.status).toBe('wrist — joint 3 of 3');
    const result = tool.onPointerDown(pointer(30, 30), ctx);
    const elements = result.created?.elements ?? [];
    expect(elements).toHaveLength(3);
    expect(elements[1]).toEqual({
      labelId: 'j-elbow',
      points: [0, 0],
      occluded: false,
      outside: true,
    });
    // The joint after the skip must still be the third element, not the second.
    expect(elements[2]?.points).toEqual([30, 30]);
  });

  it('accepts an upper-case X, because Shift is easy to leave on', () => {
    const tool = new SkeletonTool();
    const ctx = context();
    expect(tool.onKey('X', ctx)?.status).toBe('elbow — joint 2 of 3');
  });

  // A skeleton of nothing but skipped joints claims an object is present and says nothing
  // about it. There is no row worth writing.
  it('throws the skeleton away when every joint is skipped', () => {
    const tool = new SkeletonTool();
    const ctx = context();
    tool.onKey('x', ctx);
    tool.onKey('x', ctx);
    const result = tool.onKey('x', ctx);
    expect(result?.created).toBeUndefined();
    expect(result?.draft).toBeNull();
    expect(tool.decided).toBe(0);
  });

  // The first joint of a pose is as skippable as any other -- a head can be out of frame.
  // After a commit the tool is idle again, so this is the same case as skipping from the
  // start rather than a skip running past the end of the previous skeleton.
  it('can skip the first joint, including straight after committing one', () => {
    const tool = new SkeletonTool();
    const ctx = context();
    tool.onPointerDown(pointer(10, 10), ctx);
    tool.onPointerDown(pointer(20, 20), ctx);
    expect(tool.onPointerDown(pointer(30, 30), ctx).created).toBeDefined();
    expect(tool.decided).toBe(0);

    expect(tool.onKey('x', ctx)?.status).toBe('elbow — joint 2 of 3');
    expect(tool.decided).toBe(1);
    const result = tool.onPointerDown(pointer(40, 40), ctx);
    expect(result.created).toBeUndefined();
    expect(tool.onKey('Enter', ctx)?.created?.elements?.[0]?.outside).toBe(true);
  });
});

describe('finishing early and undoing', () => {
  it('Enter commits what is placed, padding the joints never reached', () => {
    const tool = new SkeletonTool();
    const ctx = context();
    tool.onPointerDown(pointer(10, 10), ctx);
    const result = tool.onKey('Enter', ctx);

    const elements = result?.created?.elements ?? [];
    expect(elements).toHaveLength(3);
    expect(elements[0]?.outside).toBe(false);
    expect(elements[1]?.outside).toBe(true);
    expect(elements[2]?.outside).toBe(true);
    expect(result?.status).toBeNull();
  });

  it('Enter before any joint is placed does nothing and leaves the key to the editor', () => {
    const tool = new SkeletonTool();
    expect(tool.onKey('Enter', context())).toBeNull();
  });

  it('Backspace takes back the last decision, placed or skipped', () => {
    const tool = new SkeletonTool();
    const ctx = context();
    tool.onPointerDown(pointer(10, 10), ctx);
    tool.onKey('x', ctx);
    expect(tool.decided).toBe(2);

    expect(tool.onKey('Backspace', ctx)?.status).toBe('elbow — joint 2 of 3');
    expect(tool.decided).toBe(1);
    expect(tool.onKey('Backspace', ctx)?.status).toBe('shoulder — joint 1 of 3');
    expect(tool.decided).toBe(0);
  });

  // Otherwise the tool would swallow the editor's delete-selection shortcut whenever the
  // skeleton tool happened to be the active one.
  it('leaves Backspace alone when no skeleton is in progress', () => {
    const tool = new SkeletonTool();
    expect(tool.onKey('Backspace', context())).toBeNull();
  });

  it('Escape throws away a skeleton in progress and only then', () => {
    const tool = new SkeletonTool();
    const ctx = context();
    expect(tool.onKey('Escape', ctx)).toBeNull();

    tool.onPointerDown(pointer(10, 10), ctx);
    const result = tool.onKey('Escape', ctx);
    expect(result?.draft).toBeNull();
    expect(result?.created).toBeUndefined();
    expect(tool.decided).toBe(0);
  });

  it('cancel clears the status line as well as the draft', () => {
    const tool = new SkeletonTool();
    expect(tool.cancel()).toEqual({ draft: null, status: null });
  });
});

describe('the draft', () => {
  it('is the shape that would be committed, not a second rendering of it', () => {
    const tool = new SkeletonTool();
    const ctx = context();
    tool.onPointerDown(pointer(10, 10), ctx);
    const draft = tool.onKey('x', ctx)?.draft;

    // Same joint count and same slots as the eventual commit, so what is on screen while
    // drawing cannot disagree with what lands.
    expect(draft?.elements).toHaveLength(3);
    expect(draft?.elements?.[0]?.points).toEqual([10, 10]);
    expect(draft?.elements?.[1]?.outside).toBe(true);
    expect(draft?.points).toEqual([10, 10]);
  });
});

describe('changing label mid-skeleton', () => {
  // Joint 2 of an arm is not joint 2 of a hand. Carrying the placement across would file
  // one label's joints into another label's slots.
  it('starts again rather than mixing one label\'s joints into another\'s slots', () => {
    const tool = new SkeletonTool();
    const scene = new Scene();
    const hand = skeletonSchema({
      id: 'hand',
      name: 'hand',
      children: [
        { id: 'h-thumb', name: 'thumb' },
        { id: 'h-index', name: 'index' },
      ],
    });
    scene.setLabels([style('arm', 'arm', SCHEMA ?? undefined), style('hand', 'hand', hand ?? undefined)]);

    let active = 'arm';
    const ctx: ToolContext = {
      scene,
      viewport: () => ({ ...createViewport(800, 600), scale: 1 }),
      imageSize: () => ({ width: 1000, height: 800 }),
      activeLabelId: () => active,
      snapEnabled: () => false,
    };

    tool.onPointerDown(pointer(10, 10), ctx);
    tool.onPointerDown(pointer(20, 20), ctx);
    expect(tool.decided).toBe(2);

    active = 'hand';
    tool.onPointerDown(pointer(30, 30), ctx);
    expect(tool.decided).toBe(1);

    const result = tool.onPointerDown(pointer(40, 40), ctx);
    expect(result.created?.labelId).toBe('hand');
    expect(result.created?.elements?.map((element) => element.labelId)).toEqual([
      'h-thumb',
      'h-index',
    ]);
  });
});
