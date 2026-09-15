/**
 * What to tell somebody who has picked up the skeleton tool.
 *
 * The skeleton tool asks for joints in a fixed order that is nowhere on the canvas, so
 * without a line of text the annotator is clicking blind through a seventeen-joint pose and
 * has no way to know which joint they are on. It also fails silently in one case that looks
 * exactly like a broken tool: the active label declares no keypoints, so every click does
 * nothing. Saying *which* labels do have joints is the difference between a dead end and a
 * next step.
 *
 * Pure, and tested without a DOM: the wording and the three states are the whole feature.
 */

import type { LabelStyle } from '@/canvas/types';

/** Keys the tool claims while a skeleton is in progress. */
export const SKELETON_KEYS = 'X skips a joint · Enter finishes · Backspace undoes one';

/** Labels in this project that actually declare joints, in project order. */
export function skeletonLabels(labels: readonly LabelStyle[]): LabelStyle[] {
  return labels.filter((label) => label.skeleton != null);
}

/**
 * The line shown above the canvas while the skeleton tool is active.
 *
 * `status` is what the engine last reported — the joint being asked for — and is null
 * before the first click and after a skeleton is committed.
 */
export function skeletonHint(
  labels: readonly LabelStyle[],
  activeLabelId: string | null,
  status: string | null,
): string {
  const active = activeLabelId ? labels.find((label) => label.id === activeLabelId) : undefined;
  const withJoints = skeletonLabels(labels);

  if (withJoints.length === 0) {
    return 'No label in this project declares keypoints, so there is no skeleton to draw. Add joints to a label in the project schema first.';
  }
  if (!active) {
    return `Choose a label with joints: ${withJoints.map((label) => label.name).join(', ')}.`;
  }
  if (!active.skeleton) {
    return `${active.name} declares no keypoints. Choose ${withJoints
      .map((label) => label.name)
      .join(' or ')} instead.`;
  }

  // Mid-skeleton: the engine's line already names the joint and the position.
  if (status) return `${status} · ${SKELETON_KEYS}`;

  const first = active.skeleton.joints[0];
  if (!first) {
    // A schema with an empty joint list should not reach here — `skeletonSchema` returns
    // null for it — but saying so beats rendering "Click undefined".
    return `${active.name} has an empty joint list.`;
  }
  return `Click ${first.name} to start the ${active.skeleton.joints.length}-joint ${active.name} · ${SKELETON_KEYS}`;
}
