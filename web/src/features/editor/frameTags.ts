/**
 * Frame tags: a label said of a whole frame rather than drawn on part of it — "night",
 * "blurred", "contains a pedestrian". CVAT calls these tags and offers them beside the
 * drawing tools; the server stored them from the start (`created_tags`/`deleted_tags` on
 * an annotation write) and only the editor could not show or make one, until iteration 102.
 *
 * A tag of a label already on the frame is not offered again: a frame is "night" once.
 */

import type { AnnotationDocument, ApiTag } from '@/api/types';
import type { LabelStyle } from '@/canvas/types';

export interface FrameTag {
  id: string;
  labelId: string;
  name: string;
  color: string;
  /** Still a model's guess, nobody having accepted it. */
  suggested: boolean;
}

/** The tags on one frame, in label order, named and coloured. */
export function tagsOn(
  document: AnnotationDocument | undefined,
  frame: number,
  labels: readonly LabelStyle[],
): FrameTag[] {
  if (!document) return [];
  const order = new Map(labels.map((label, index) => [label.id, index]));
  return document.tags
    .filter((tag: ApiTag) => tag.frame === frame)
    .map((tag) => {
      const label = labels.find((candidate) => candidate.id === tag.label_id);
      return {
        id: tag.id,
        labelId: tag.label_id,
        name: label?.name ?? 'unknown label',
        color: label?.color ?? '#94a3b8',
        suggested: tag.source === 'model' && tag.confidence !== null,
      };
    })
    .sort((a, b) => (order.get(a.labelId) ?? 1e9) - (order.get(b.labelId) ?? 1e9));
}

/**
 * The labels a frame can still be tagged with: the project's own labels, not a skeleton's
 * joints, and not one the frame already carries.
 */
export function taggableLabels(
  labels: readonly LabelStyle[],
  present: readonly FrameTag[],
): LabelStyle[] {
  const taken = new Set(present.map((tag) => tag.labelId));
  return labels.filter((label) => label.parentId == null && !taken.has(label.id));
}
