import { beforeEach, describe, expect, it } from 'vitest';
import {
  CommandStack,
  createAddCommand,
  createDeleteCommand,
  createUpdateCommand,
  type CommandTarget,
} from '../commands';
import type { Annotation } from '../types';

function annotation(id: string, x = 0): Annotation {
  return {
    id,
    labelId: 'label',
    frame: 0,
    shapeType: 'rectangle',
    points: [x, 0, x + 10, 10],
    rotation: 0,
    occluded: false,
    outside: false,
    zOrder: 0,
    source: 'manual',
    attributes: {},
  };
}

function createTarget(): CommandTarget & { store: Map<string, Annotation> } {
  const store = new Map<string, Annotation>();
  return {
    store,
    add: (a) => void store.set(a.id, a),
    update: (a) => void store.set(a.id, a),
    remove: (id) => void store.delete(id),
    get: (id) => store.get(id),
  };
}

describe('CommandStack', () => {
  let target: ReturnType<typeof createTarget>;
  let stack: CommandStack;

  beforeEach(() => {
    target = createTarget();
    stack = new CommandStack();
  });

  it('applies a command and can undo it', () => {
    stack.execute(createAddCommand(target, annotation('a')));
    expect(target.store.has('a')).toBe(true);

    expect(stack.undo()).toBe(true);
    expect(target.store.has('a')).toBe(false);
  });

  it('redoes an undone command', () => {
    stack.execute(createAddCommand(target, annotation('a')));
    stack.undo();
    expect(stack.redo()).toBe(true);
    expect(target.store.has('a')).toBe(true);
  });

  it('reports what is available to undo and redo', () => {
    expect(stack.canUndo).toBe(false);
    expect(stack.undoLabel).toBeNull();

    stack.execute(createAddCommand(target, annotation('a')));
    expect(stack.canUndo).toBe(true);
    expect(stack.undoLabel).toBe('Add rectangle');

    stack.undo();
    expect(stack.canRedo).toBe(true);
    expect(stack.redoLabel).toBe('Add rectangle');
  });

  it('discards the redo history once new work happens', () => {
    stack.execute(createAddCommand(target, annotation('a')));
    stack.undo();
    stack.execute(createAddCommand(target, annotation('b')));

    expect(stack.canRedo).toBe(false);
    expect(target.store.has('b')).toBe(true);
  });

  it('does not record the commands replayed by an undo', () => {
    stack.execute(createAddCommand(target, annotation('a')));
    stack.undo();
    stack.redo();
    // One entry, not three: replaying must not grow the history.
    expect(stack.canUndo).toBe(true);
    stack.undo();
    expect(stack.canUndo).toBe(false);
  });

  it('bounds its history so a long session cannot exhaust memory', () => {
    const bounded = new CommandStack({ limit: 3 });
    for (let i = 0; i < 10; i++) {
      bounded.execute(createAddCommand(target, annotation(`s${i}`)));
    }
    let undone = 0;
    while (bounded.undo()) undone++;
    expect(undone).toBe(3);
  });

  it('reports nothing to undo on an empty stack', () => {
    expect(stack.undo()).toBe(false);
    expect(stack.redo()).toBe(false);
  });
});

describe('delete command', () => {
  it('restores every deleted object on undo', () => {
    const target = createTarget();
    const stack = new CommandStack();
    const objects = [annotation('a'), annotation('b')];
    for (const object of objects) target.add(object);

    stack.execute(createDeleteCommand(target, objects));
    expect(target.store.size).toBe(0);

    stack.undo();
    expect(target.store.size).toBe(2);
  });
});

describe('update coalescing', () => {
  it('merges a drag into one undo entry while keeping the original state', () => {
    const target = createTarget();
    const stack = new CommandStack();
    const original = annotation('a', 0);
    target.add(original);

    // Three pointer-move steps of the same drag.
    let previous = original;
    for (const x of [5, 10, 15]) {
      const next = { ...previous, points: [x, 0, x + 10, 10] };
      stack.execute(createUpdateCommand(target, [original], [next]));
      previous = next;
    }
    expect(target.store.get('a')!.points[0]).toBe(15);

    stack.undo();
    // One undo, straight back to where the drag started.
    expect(target.store.get('a')!.points[0]).toBe(0);
    expect(stack.canUndo).toBe(false);
  });

  it('does not merge edits to different objects', () => {
    const target = createTarget();
    const stack = new CommandStack();
    target.add(annotation('a'));
    target.add(annotation('b'));

    stack.execute(createUpdateCommand(target, [annotation('a')], [annotation('a', 20)]));
    stack.execute(createUpdateCommand(target, [annotation('b')], [annotation('b', 20)]));

    stack.undo();
    expect(target.store.get('b')!.points[0]).toBe(0);
    expect(target.store.get('a')!.points[0]).toBe(20);
    expect(stack.canUndo).toBe(true);
  });

  it('does not merge edits with different labels', () => {
    const target = createTarget();
    const stack = new CommandStack();
    target.add(annotation('a'));

    stack.execute(createUpdateCommand(target, [annotation('a')], [annotation('a', 5)], 'Move'));
    stack.execute(
      createUpdateCommand(target, [annotation('a', 5)], [annotation('a', 9)], 'Change label'),
    );

    stack.undo();
    expect(target.store.get('a')!.points[0]).toBe(5);
    expect(stack.canUndo).toBe(true);
  });
});
