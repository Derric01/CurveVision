/**
 * The editor's side of click-to-segment (`canvas/interactor.ts`): which models can answer
 * clicks, and what in a model's answer is the outline.
 */

import type { InteractorAnswer } from '@/canvas/interactor';
import type { ModelRegistration } from '@/api/types';

/** Active `interactor` models: the ones that take clicks and return an outline. */
export function interactorModels(models: readonly ModelRegistration[] | undefined): ModelRegistration[] {
  return (models ?? []).filter((model) => model.is_active && model.kind === 'interactor');
}

/**
 * The outline in a model's answer: its most confident polygon on the frame asked about.
 *
 * The contract lets a model return several shapes; an interactor answering clicks on one
 * object returns one, and when it returns more the most confident is what the clicks meant.
 * A model that answers with something other than a polygon is said to, rather than its
 * answer dropped as if it had found nothing.
 */
export function outlineFrom(shapes: readonly Record<string, unknown>[], frame: number): InteractorAnswer {
  const onFrame = shapes.filter((shape) => shape.frame === undefined || shape.frame === frame);
  const polygons = onFrame
    .filter((shape) => shape.type === 'polygon' && Array.isArray(shape.points))
    .map((shape) => ({
      points: (shape.points as unknown[]).map(Number),
      confidence: typeof shape.confidence === 'number' ? shape.confidence : 1,
    }))
    .filter((shape) => shape.points.length >= 6 && shape.points.every(Number.isFinite));
  if (polygons.length === 0) {
    const other = onFrame.find((shape) => typeof shape.type === 'string')?.type;
    return other
      ? { error: `The model answered with a ${String(other)}, not an outline` }
      : { points: [] };
  }
  polygons.sort((a, b) => b.confidence - a.confidence);
  return { points: polygons[0]!.points };
}
