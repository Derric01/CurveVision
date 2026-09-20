/**
 * Reviewing what a model proposed.
 *
 * Iteration 34 gave the editor a way to run a model; this is the other half. `POST
 * /jobs/{id}/suggestions` and the client's `decideSuggestions` had both existed for many
 * iterations with nothing calling them, so a job could accumulate predictions that nobody
 * could mark as reviewed — only edit one at a time, or delete them.
 *
 * The state itself — what separates a suggestion awaiting a decision from one already
 * accepted — is `isUnreviewed` in `canvas/types.ts`, stated once there because the renderer
 * and the scene both turn on it. This module is what the *editor* needs on top of it: the
 * job-wide set, split the way the endpoint takes it, and the copy.
 */

import type { AnnotationDocument } from '@/api/types';
import { isUnreviewed } from '@/canvas/types';

/** What a decision call has to be given, split the way the endpoint takes it. */
export interface ReviewSet {
  shapeIds: string[];
  trackIds: string[];
  tagIds: string[];
  total: number;
}

export const NOTHING_TO_REVIEW: ReviewSet = {
  shapeIds: [],
  trackIds: [],
  tagIds: [],
  total: 0,
};

/**
 * Everything in this job still awaiting a decision.
 *
 * Job-wide rather than per-frame on purpose: a run covers the whole job by default, so a
 * frame-by-frame count would understate what is outstanding and an "accept all" scoped to
 * the visible frame would leave the rest silently pending.
 */
export function pendingReview(document: AnnotationDocument | undefined): ReviewSet {
  if (!document) return NOTHING_TO_REVIEW;
  const shapeIds = document.shapes.filter(isUnreviewed).map((shape) => shape.id);
  const trackIds = document.tracks.filter(isUnreviewed).map((track) => track.id);
  const tagIds = document.tags.filter(isUnreviewed).map((tag) => tag.id);
  return {
    shapeIds,
    trackIds,
    tagIds,
    total: shapeIds.length + trackIds.length + tagIds.length,
  };
}

/**
 * The heading over the two buttons.
 *
 * It names the job, not the frame, because that is the scope the buttons act on and the
 * count will often be larger than what is on screen.
 */
export function describePending(total: number): string {
  if (total === 0) return 'Nothing is waiting for review.';
  return total === 1
    ? '1 suggestion in this job is waiting for review.'
    : `${total} suggestions in this job are waiting for review.`;
}

/** What the server reported back, in one line. */
export function describeDecision(
  counts: Record<string, number> | undefined,
  accepted: boolean,
): string {
  const total = Object.values(counts ?? {}).reduce((sum, count) => sum + count, 0);
  const things = total === 1 ? 'suggestion' : 'suggestions';
  if (total === 0) return accepted ? 'Nothing was accepted.' : 'Nothing was rejected.';
  return accepted
    ? `Kept ${total} ${things}. They are ordinary annotations now.`
    : `Deleted ${total} ${things}.`;
}
