/**
 * The click-to-segment tool: clicks become prompts, the newest answer is the outline,
 * Enter keeps it. The model is a fake whose answers this test releases in any order.
 */

import { describe, expect, it } from 'vitest';

import { describeInteraction, InteractorTool, type InteractorPrompt } from '../interactor';
import { Scene } from '../scene';
import { createTool, TOOL_SHORTCUTS, type ToolContext, type ToolResult } from '../tools';
import { createViewport } from '../viewport';

interface Pending {
  prompt: InteractorPrompt;
  answer: (points: number[] | { error: string }) => void;
  fail: (reason: Error) => void;
}

function harness(options: { model?: boolean; frame?: () => number } = {}) {
  const asked: Pending[] = [];
  const delivered: ToolResult[] = [];
  const context: ToolContext = {
    scene: new Scene({}),
    viewport: () => createViewport(),
    imageSize: () => ({ width: 200, height: 100 }),
    activeLabelId: () => 'label-1',
    snapEnabled: () => false,
    currentFrame: options.frame ?? (() => 4),
    interact:
      options.model === false
        ? null
        : (prompt) =>
            new Promise((resolve, reject) => {
              asked.push({
                prompt,
                answer: (value) => resolve(Array.isArray(value) ? { points: value } : value),
                fail: reject,
              });
            }),
    deliver: (result) => delivered.push(result),
  };
  return { tool: new InteractorTool(), context, asked, delivered };
}

const click = (x: number, y: number, button = 0, altKey = false) => ({
  image: { x, y },
  button,
  altKey,
});

const SQUARE = [10, 10, 60, 10, 60, 60, 10, 60];
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('the interactor tool', () => {
  it('is reached by its key', () => {
    expect(TOOL_SHORTCUTS.i).toBe('interactor');
    expect(createTool('interactor')).toBeInstanceOf(InteractorTool);
  });

  it('sends clicks on the object and off it, in image pixels', () => {
    const { tool, context, asked } = harness();
    tool.onPointerDown(click(30, 30), context);
    tool.onPointerDown(click(90, 40, 2), context);
    tool.onPointerDown(click(95, 45, 0, true), context);
    expect(asked.map((pending) => pending.prompt)).toEqual([
      { positive: [[30, 30]], negative: [] },
      { positive: [[30, 30]], negative: [[90, 40]] },
      { positive: [[30, 30]], negative: [[90, 40], [95, 45]] },
    ]);
  });

  it('shows the answer as a polygon, and Enter keeps it as a model-drawn shape', async () => {
    const { tool, context, asked, delivered } = harness();
    const first = tool.onPointerDown(click(30, 30), context);
    expect(first.status).toContain('Asking the model');
    expect(first.draft?.shapeType).toBe('points');

    asked[0]!.answer(SQUARE);
    await settle();
    expect(delivered.at(-1)?.draft).toMatchObject({ shapeType: 'polygon', points: SQUARE });
    expect(delivered.at(-1)?.status).toContain('Enter keeps this outline');

    const done = tool.onKey('Enter', context)!;
    expect(done.created).toMatchObject({
      shapeType: 'polygon',
      points: SQUARE,
      labelId: 'label-1',
      source: 'model',
    });
    expect(done.created?.confidence ?? null).toBeNull();
    expect(tool.clickCount).toBe(0);
  });

  it('keeps the newest answer when an older one arrives late', async () => {
    const { tool, context, asked, delivered } = harness();
    tool.onPointerDown(click(30, 30), context);
    tool.onPointerDown(click(40, 40), context);
    asked[1]!.answer([0, 0, 5, 0, 5, 5]);
    await settle();
    asked[0]!.answer(SQUARE); // late, for clicks that have since changed
    await settle();
    expect(delivered).toHaveLength(1);
    expect(tool.onKey('Enter', context)!.created?.points).toEqual([0, 0, 5, 0, 5, 5]);
  });

  it('will not keep an outline while the model is still answering', () => {
    const { tool, context } = harness();
    tool.onPointerDown(click(30, 30), context);
    const early = tool.onKey('Enter', context)!;
    expect(early.created).toBeUndefined();
    expect(early.status).toContain('Still waiting');
  });

  it('Backspace takes back a click and asks again; the last one clears the outline', async () => {
    const { tool, context, asked, delivered } = harness();
    tool.onPointerDown(click(30, 30), context);
    tool.onPointerDown(click(90, 40, 2), context);
    asked[1]!.answer(SQUARE);
    await settle();

    tool.onKey('Backspace', context);
    expect(asked.at(-1)!.prompt).toEqual({ positive: [[30, 30]], negative: [] });
    const cleared = tool.onKey('Backspace', context)!;
    expect(cleared.draft).toBeNull();
    asked.at(-1)!.answer(SQUARE); // the answer to a click that was taken back
    await settle();
    expect(delivered).toHaveLength(1);
    expect(tool.onKey('Enter', context)!.created).toBeUndefined();
  });

  it('says what went wrong when the model cannot answer, and keeps the clicks', async () => {
    const { tool, context, asked, delivered } = harness();
    tool.onPointerDown(click(30, 30), context);
    asked[0]!.fail(new Error('The model server answered 503'));
    await settle();
    expect(delivered.at(-1)?.status).toContain('The model server answered 503');
    expect(tool.clickCount).toBe(1);
  });

  it('starts again on another frame rather than outlining across two', async () => {
    let frame = 4;
    const { tool, context, asked } = harness({ frame: () => frame });
    tool.onPointerDown(click(30, 30), context);
    asked[0]!.answer(SQUARE);
    await settle();
    frame = 5;
    expect(tool.onKey('Enter', context)!.created).toBeUndefined();
  });

  it('says how to get a model when the workspace has none', () => {
    const { tool, context, asked } = harness({ model: false });
    const result = tool.onPointerDown(click(30, 30), context);
    expect(result.status).toContain('Models page');
    expect(asked).toHaveLength(0);
  });

  it('Escape drops everything, including an answer still on its way', async () => {
    const { tool, context, asked, delivered } = harness();
    tool.onPointerDown(click(30, 30), context);
    tool.onKey('Escape', context);
    asked[0]!.answer(SQUARE);
    await settle();
    expect(delivered).toHaveLength(0);
  });
});

describe('what the tool says', () => {
  it('names the clicks on and off the object', () => {
    expect(
      describeInteraction({ available: true, positive: 2, negative: 1, waiting: false, outlined: true }),
    ).toContain('2 on it, 1 off it');
    expect(
      describeInteraction({ available: true, positive: 1, negative: 0, waiting: false, outlined: false }),
    ).toContain('found nothing');
  });
});
