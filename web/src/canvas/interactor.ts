/**
 * Click-to-segment: an `interactor` model outlines the object the annotator clicks on.
 *
 * CVAT's interactors (SAM among them) are the model: left-click on the object, right-click
 * (or Alt-click) on what is not it, and after each click the model's outline is redrawn;
 * `Enter` keeps it as a polygon. The model runs on the server — the page sends the clicks
 * through `POST /jobs/{id}/inference` with `persist: false` and the editor previews what
 * comes back — so this tool only collects clicks, asks, and shows the latest answer.
 *
 * Answers arrive after the click that asked for them, and a later click can be answered
 * first. Each request is numbered and only the newest is shown, so a slow answer to an
 * old set of clicks never replaces the outline for the current one.
 */

import type { Annotation, Point, ToolName } from './types';
import type { Tool, ToolContext, ToolResult } from './tools';

/** The clicks, in image pixels, as the inference contract's `prompts` carry them. */
export interface InteractorPrompt {
  positive: [number, number][];
  negative: [number, number][];
}

/** A model's outline as flattened polygon points, or why there is none. */
export type InteractorAnswer = { points: number[] } | { error: string };

export type Interact = (prompt: InteractorPrompt) => Promise<InteractorAnswer>;

interface Click {
  point: Point;
  positive: boolean;
}

/** The line the editor shows while the tool is active. */
export function describeInteraction(state: {
  available: boolean;
  positive: number;
  negative: number;
  waiting: boolean;
  outlined: boolean;
  error?: string | null;
}): string {
  if (!state.available) {
    return 'No interactor model in this workspace — add one on the Models page.';
  }
  if (state.positive === 0) {
    return 'Click the object to outline it. Right-click (or Alt-click) what is not part of it.';
  }
  const clicks =
    `${state.positive} on it` + (state.negative > 0 ? `, ${state.negative} off it` : '');
  if (state.waiting) return `Asking the model… (${clicks})`;
  if (state.error) return `${state.error} (${clicks}) — click again, or Backspace.`;
  if (!state.outlined) return `The model found nothing there (${clicks}).`;
  return `Enter keeps this outline · click to refine (${clicks}) · Backspace undoes a click`;
}

export class InteractorTool implements Tool {
  readonly name: ToolName = 'interactor';
  readonly cursor = 'crosshair';

  private clicks: Click[] = [];
  private outline: number[] | null = null;
  private labelId: string | null = null;
  private frame: number | null = null;
  private waiting = false;
  private error: string | null = null;
  /** The number of the newest request; an answer to any other is dropped. */
  private sequence = 0;

  onPointerDown(input: { image: Point; button: number; altKey: boolean }, context: ToolContext): ToolResult {
    if (input.button !== 0 && input.button !== 2) return {};
    if (!context.interact || !context.deliver) return { status: this.status(false) };
    const labelId = context.activeLabelId();
    if (!labelId) return {};
    const frame = context.currentFrame?.() ?? null;
    if (this.frame !== null && frame !== this.frame) this.reset();
    this.frame = frame;
    this.labelId = labelId;
    this.clicks.push({ point: input.image, positive: input.button === 0 && !input.altKey });
    return this.ask(context);
  }

  onPointerMove(): ToolResult {
    return {};
  }

  onPointerUp(): ToolResult {
    return {};
  }

  onKey(key: string, context: ToolContext): ToolResult | null {
    if (key === 'Enter') return this.finish(context);
    if (key === 'Escape') return this.cancel();
    if (key === 'Backspace' && this.clicks.length > 0) {
      this.clicks.pop();
      if (this.positives().length === 0) {
        // Nothing left to segment: what is on screen answers clicks that no longer exist.
        this.sequence += 1;
        this.outline = null;
        this.waiting = false;
        this.error = null;
        return { draft: this.draft(), status: this.status(Boolean(context.interact)) };
      }
      return this.ask(context);
    }
    return null;
  }

  finish(context?: ToolContext): ToolResult {
    if (this.waiting) {
      return { status: 'Still waiting for the model — Enter again once the outline appears.' };
    }
    if (!this.outline || this.outline.length < 6 || !this.labelId) return this.cancel();
    const frame = context?.currentFrame?.() ?? this.frame;
    if (this.frame !== null && frame !== this.frame) return this.cancel();
    const created: Annotation = {
      ...outlineDraft(this.labelId, this.outline),
      pending: true,
      // A model drew it, and the annotator accepted it by pressing Enter: the same pair
      // of facts an accepted suggestion records.
      source: 'model',
    };
    this.reset();
    return { created, draft: null, status: null };
  }

  cancel(): ToolResult {
    this.reset();
    return { draft: null, status: null };
  }

  private ask(context: ToolContext): ToolResult {
    const positive = this.positives();
    if (positive.length === 0) {
      return { draft: this.draft(), status: this.status(true) };
    }
    const sequence = ++this.sequence;
    this.waiting = true;
    this.error = null;
    const prompt: InteractorPrompt = {
      positive: positive.map(({ point }) => [point.x, point.y]),
      negative: this.clicks
        .filter((click) => !click.positive)
        .map(({ point }) => [point.x, point.y]),
    };
    const deliver = context.deliver!;
    context.interact!(prompt).then(
      (answer) => {
        if (sequence !== this.sequence) return;
        this.waiting = false;
        if ('error' in answer) {
          this.error = answer.error;
        } else {
          this.outline = answer.points.length >= 6 ? answer.points : null;
        }
        deliver({ draft: this.draft(), status: this.status(true) });
      },
      (reason: unknown) => {
        if (sequence !== this.sequence) return;
        this.waiting = false;
        this.error = reason instanceof Error ? reason.message : 'The model could not be asked';
        deliver({ draft: this.draft(), status: this.status(true) });
      },
    );
    return { draft: this.draft(), status: this.status(true) };
  }

  private positives(): Click[] {
    return this.clicks.filter((click) => click.positive);
  }

  /** The outline when there is one, else the clicks on the object as points. */
  private draft(): Annotation | null {
    if (!this.labelId) return null;
    if (this.outline) return outlineDraft(this.labelId, this.outline);
    const positive = this.positives();
    if (positive.length === 0) return null;
    return {
      ...outlineDraft(this.labelId, positive.flatMap(({ point }) => [point.x, point.y])),
      shapeType: 'points',
    };
  }

  private status(available: boolean): string {
    return describeInteraction({
      available,
      positive: this.positives().length,
      negative: this.clicks.length - this.positives().length,
      waiting: this.waiting,
      outlined: this.outline !== null,
      error: this.error,
    });
  }

  private reset(): void {
    // Invalidates any answer still on its way.
    this.sequence += 1;
    this.clicks = [];
    this.outline = null;
    this.labelId = null;
    this.frame = null;
    this.waiting = false;
    this.error = null;
  }

  get clickCount(): number {
    return this.clicks.length;
  }
}

let draftCounter = 0;

function outlineDraft(labelId: string, points: number[]): Annotation {
  draftCounter += 1;
  const id = `local-interactor-${Date.now().toString(36)}-${draftCounter}`;
  return {
    id,
    clientId: id,
    labelId,
    frame: 0,
    shapeType: 'polygon',
    points: [...points],
    rotation: 0,
    occluded: false,
    outside: false,
    zOrder: 0,
    source: 'manual',
    attributes: {},
    pending: true,
  };
}
