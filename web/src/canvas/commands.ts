/**
 * Undo/redo.
 *
 * A command stack of inverse pairs rather than snapshots: a job with 100k annotations
 * cannot afford a full copy per edit, and coalescing a drag into one entry is what makes
 * undo behave the way people expect (one Ctrl+Z per gesture, not per pointer-move).
 */

import type { Annotation } from './types';

export interface Command {
  /** Shown in the history UI. */
  label: string;
  apply(): void;
  revert(): void;
  /**
   * Merge a newer command of the same kind into this one, returning true if it was
   * absorbed. Used so a drag emits one undo entry instead of hundreds.
   */
  coalesceWith?(next: Command): boolean;
}

export interface CommandStackOptions {
  /** How many commands to remember. Bounded so a long session cannot exhaust memory. */
  limit?: number;
  onChange?: () => void;
}

export class CommandStack {
  private undoStack: Command[] = [];
  private redoStack: Command[] = [];
  private readonly limit: number;
  private readonly onChange?: () => void;
  /** While true, commands executed are not recorded -- used when replaying an undo. */
  private suppressed = false;

  constructor(options: CommandStackOptions = {}) {
    this.limit = options.limit ?? 200;
    this.onChange = options.onChange;
  }

  get canUndo(): boolean {
    return this.undoStack.length > 0;
  }

  get canRedo(): boolean {
    return this.redoStack.length > 0;
  }

  get undoLabel(): string | null {
    return this.undoStack.at(-1)?.label ?? null;
  }

  get redoLabel(): string | null {
    return this.redoStack.at(-1)?.label ?? null;
  }

  /** Run a command and record it. */
  execute(command: Command): void {
    command.apply();
    if (this.suppressed) return;

    const previous = this.undoStack.at(-1);
    if (previous?.coalesceWith?.(command)) {
      // Absorbed into the previous entry; a redo history built on the old state would be
      // wrong, so it is discarded exactly as it would be for a new entry.
      this.redoStack.length = 0;
      this.onChange?.();
      return;
    }

    this.undoStack.push(command);
    if (this.undoStack.length > this.limit) this.undoStack.shift();
    this.redoStack.length = 0;
    this.onChange?.();
  }

  undo(): boolean {
    const command = this.undoStack.pop();
    if (!command) return false;
    this.suppressed = true;
    try {
      command.revert();
    } finally {
      this.suppressed = false;
    }
    this.redoStack.push(command);
    this.onChange?.();
    return true;
  }

  redo(): boolean {
    const command = this.redoStack.pop();
    if (!command) return false;
    this.suppressed = true;
    try {
      command.apply();
    } finally {
      this.suppressed = false;
    }
    this.undoStack.push(command);
    this.onChange?.();
    return true;
  }

  clear(): void {
    this.undoStack.length = 0;
    this.redoStack.length = 0;
    this.onChange?.();
  }
}

/** What a command needs from the scene to apply and revert itself. */
export interface CommandTarget {
  add(annotation: Annotation): void;
  update(annotation: Annotation): void;
  remove(id: string): void;
  get(id: string): Annotation | undefined;
}

export function createAddCommand(target: CommandTarget, annotation: Annotation): Command {
  return {
    label: `Add ${annotation.shapeType}`,
    apply: () => target.add(annotation),
    revert: () => target.remove(annotation.id),
  };
}

export function createDeleteCommand(target: CommandTarget, annotations: Annotation[]): Command {
  const snapshot = annotations.map((annotation) => ({ ...annotation }));
  return {
    label: snapshot.length === 1 ? 'Delete object' : `Delete ${snapshot.length} objects`,
    apply: () => {
      for (const annotation of snapshot) target.remove(annotation.id);
    },
    revert: () => {
      for (const annotation of snapshot) target.add({ ...annotation });
    },
  };
}

/**
 * An edit to existing objects.
 *
 * `coalesceWith` merges consecutive edits touching the same object set, which turns a
 * drag into a single undo entry while keeping the *original* before-state.
 */
export function createUpdateCommand(
  target: CommandTarget,
  before: Annotation[],
  after: Annotation[],
  label = 'Edit object',
): Command {
  const beforeSnapshot = before.map((annotation) => ({ ...annotation }));
  let afterSnapshot = after.map((annotation) => ({ ...annotation }));
  const ids = new Set(beforeSnapshot.map((annotation) => annotation.id));

  const command: Command = {
    label,
    apply: () => {
      for (const annotation of afterSnapshot) target.update({ ...annotation });
    },
    revert: () => {
      for (const annotation of beforeSnapshot) target.update({ ...annotation });
    },
    coalesceWith: (next) => {
      const candidate = next as Command & { __updateIds?: Set<string>; __after?: Annotation[] };
      if (!candidate.__updateIds || candidate.label !== label) return false;
      if (candidate.__updateIds.size !== ids.size) return false;
      for (const id of candidate.__updateIds) {
        if (!ids.has(id)) return false;
      }
      afterSnapshot = (candidate.__after ?? []).map((annotation) => ({ ...annotation }));
      return true;
    },
  };

  Object.assign(command, { __updateIds: ids, __after: afterSnapshot });
  return command;
}
