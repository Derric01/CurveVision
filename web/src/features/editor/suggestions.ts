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

/**
 * The suggestions among the selected objects, split the way the endpoint takes them.
 *
 * Selection ids are shape ids, or a track's id for any frame of a track. Tags have no
 * object on the canvas, so they are only ever decided in bulk.
 */
export function selectedForReview(
  document: AnnotationDocument | undefined,
  selection: readonly string[],
): ReviewSet {
  if (!document || selection.length === 0) return NOTHING_TO_REVIEW;
  const chosen = new Set(selection);
  const shapeIds = document.shapes
    .filter((shape) => chosen.has(shape.id) && isUnreviewed(shape))
    .map((shape) => shape.id);
  const trackIds = document.tracks
    .filter((track) => chosen.has(track.id) && isUnreviewed(track))
    .map((track) => track.id);
  return { shapeIds, trackIds, tagIds: [], total: shapeIds.length + trackIds.length };
}

/** Where a suggestion is: the frame it is on (a track's first), its id, and how sure the model was. */
export interface SuggestionPlace {
  frame: number;
  id: string;
  confidence: number;
}

/**
 * The order *Next suggestion* walks in.
 *
 * `frame` goes through the job as it plays. `confidence` goes least sure first — the
 * active-learning order: where the model was least confident is where a person's decision
 * corrects it most, and a reviewer short of time spends it there rather than confirming
 * what the model already got right.
 */
export type ReviewOrder = 'frame' | 'confidence';

/**
 * The next suggestion awaiting a decision after the one in view, in `order`, and round to
 * the start after the last.
 *
 * `after` is where the annotator is: the frame on screen and, if one is selected, the
 * suggestion selected there, so pressing *Next* again moves on rather than finding the
 * same one. `null` when nothing is waiting.
 */
export function nextSuggestion(
  document: AnnotationDocument | undefined,
  after: { frame: number; id?: string | null },
  order: ReviewOrder = 'frame',
): SuggestionPlace | null {
  if (!document) return null;
  const places: SuggestionPlace[] = [
    ...document.shapes.filter(isUnreviewed).map((shape) => ({
      frame: shape.frame,
      id: shape.id,
      confidence: shape.confidence ?? 1,
    })),
    ...document.tracks
      .filter((track) => isUnreviewed(track) && track.shapes.length > 0)
      .map((track) => ({
        frame: Math.min(...track.shapes.map((position) => position.frame)),
        id: track.id,
        confidence: track.confidence ?? 1,
      })),
  ];
  if (places.length === 0) return null;
  const byFrame = (a: SuggestionPlace, b: SuggestionPlace) =>
    a.frame - b.frame || a.id.localeCompare(b.id);
  places.sort(
    order === 'confidence' ? (a, b) => a.confidence - b.confidence || byFrame(a, b) : byFrame,
  );
  const here = after.id ? places.findIndex((place) => place.id === after.id) : -1;
  if (here >= 0) return places[(here + 1) % places.length]!;
  if (order === 'confidence') return places[0]!;
  return places.find((place) => place.frame >= after.frame) ?? places[0]!;
}

/** How sure the model was, as a person reads it: "62% sure". `null` for a non-suggestion. */
export function describeConfidence(confidence: number | null | undefined): string | null {
  if (confidence == null) return null;
  return `${Math.round(confidence * 100)}% sure`;
}
