/**
 * Reading a job's issues.
 *
 * An issue is how a reviewer sends work back: a comment thread anchored to a frame, and
 * optionally to the object being complained about. The API, the model and the permissions
 * have existed since the first iteration; nothing in the editor called them, so the only way
 * to receive review feedback was to read it out of the database.
 *
 * The ordering decisions are the substance here, and each one follows from what a reviewer
 * or an annotator is actually doing:
 *
 * **Open before resolved, open in frame order.** Open issues are work; resolved ones are
 * history. Walking down the open list should walk forwards through the video, the same rule
 * the quality panel's conflict list follows, because it is the same motion.
 *
 * **Resolved newest-first.** History reads backwards — the thing resolved a minute ago is
 * the one you might want to reopen, not the one resolved last week.
 *
 * **The preview is the first comment, not the latest.** The first comment is what the issue
 * *is*; later ones are the conversation about it. A list showing the latest would retitle a
 * thread every time somebody replied, and "ok, thanks" is not what the row should say.
 */

import type { Annotation } from '@/canvas/types';
import type { Issue } from '@/api/types';

export interface IssueRow {
  issue: Issue;
  /** What the issue is: its opening comment. */
  preview: string;
  /** Who opened it, when the server knows. */
  author: string | null;
  /** Comments beyond the opening one. */
  replies: number;
  /** True when the frame under discussion is the one on screen. */
  onCurrentFrame: boolean;
}

export interface IssueLists {
  open: IssueRow[];
  resolved: IssueRow[];
}

function toRow(issue: Issue, currentFrame: number): IssueRow {
  const comments = issue.comments ?? [];
  const first = comments[0];
  return {
    issue,
    preview: first?.body?.trim() ?? '',
    author: first?.author?.username ?? null,
    replies: Math.max(0, comments.length - 1),
    onCurrentFrame: issue.frame === currentFrame,
  };
}

/** Issues split into work and history, each in the order they are read in. */
export function issueRows(
  issues: Issue[] | undefined | null,
  currentFrame: number,
): IssueLists {
  const rows = (issues ?? []).map((issue) => toRow(issue, currentFrame));
  const open = rows
    .filter((row) => row.issue.state === 'open')
    .sort(
      (a, b) =>
        a.issue.frame - b.issue.frame ||
        a.issue.created_at.localeCompare(b.issue.created_at),
    );
  const resolved = rows
    .filter((row) => row.issue.state !== 'open')
    .sort((a, b) => {
      // `resolved_at` is the useful clock here, but an issue can be marked resolved by a
      // client that did not have one; fall back rather than dropping it to the bottom.
      const left = a.issue.resolved_at ?? a.issue.created_at;
      const right = b.issue.resolved_at ?? b.issue.created_at;
      return right.localeCompare(left);
    });
  return { open, resolved };
}

/** How many are outstanding, for a count beside the heading. */
export function openCount(issues: Issue[] | undefined | null): number {
  return (issues ?? []).filter((issue) => issue.state === 'open').length;
}

/**
 * What the new issue should be attached to.
 *
 * A selected object is by far the most common thing a review comment is about — "this box is
 * wrong" — and the editor already knows which one is selected, so anchoring costs nothing.
 * A track and a shape are different columns on the API, and the editor's annotation id is
 * the track's id when it came from one, so the caller cannot just send `shape_id` and hope.
 */
export function anchorFor(
  annotation: Annotation | undefined | null,
): { shape_id?: string; track_id?: string } {
  if (!annotation) return {};
  // A materialised track position is not a shape and has no shape row to point at; its id
  // is the track's. Sending it as `shape_id` would set a foreign key to a row that does not
  // exist in that table, and the anchor would silently be dropped.
  if (annotation.trackId) return { track_id: annotation.trackId };
  // A shape that has not been saved yet has no server id to anchor to.
  if (annotation.pending) return {};
  return { shape_id: annotation.id };
}

/** A short description of where an issue points, for the row. */
export function describeAnchor(issue: Issue): string {
  const where = `frame ${issue.frame}`;
  if (issue.shape_id || issue.track_id) return `${where} · on an object`;
  return where;
}
