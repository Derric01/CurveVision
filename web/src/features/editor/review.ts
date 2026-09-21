/**
 * Accepting or sending back submitted work.
 *
 * `POST /jobs/{id}/review` and the client's `reviewJob` have existed since the first
 * iterations with **nothing calling them**: an annotator could submit a job from the editor
 * and no reviewer could do anything about it from the application at all. The state machine
 * `docs/ROADMAP.md` calls Done was reachable only from the SDK, the CLI or curl — the same
 * shape of gap as issues having a model, an API and permissions but no panel, and model
 * suggestions having a decision endpoint nothing called.
 *
 * This module is the decisions and the copy; `ReviewPanel.tsx` is the wiring. Pure, because
 * the rules here — which states can be reviewed at all, and what a rejection owes the
 * annotator — are worth pinning without a DOM.
 */

import type { JobState } from '@/api/types';

/**
 * Whether the review controls apply at all.
 *
 * Only a submitted job can be reviewed: the server refuses anything else with "Only
 * submitted jobs can be reviewed", so offering the buttons on a job in any other state
 * would be offering an action that cannot succeed.
 */
export function isReviewable(state: JobState | undefined): boolean {
  return state === 'submitted';
}

/**
 * Whether the editor's Submit button can do anything.
 *
 * Mirrors the one transition that button performs against the server's own
 * `ALLOWED_TRANSITIONS`: `new`, `in_progress` and `rejected` may be submitted, and a
 * rejected job being resubmitted is the whole point of sending one back. An **accepted**
 * job may not — the header used to disable Submit only for a job already `submitted`, so
 * pressing it on an accepted one sent a request the server refuses with a 409 that nothing
 * rendered. A button whose only outcome is a silent failure is worse than a disabled one.
 */
export function canSubmit(state: JobState | undefined): boolean {
  return state === 'new' || state === 'in_progress' || state === 'rejected';
}

/**
 * Whether Reject may be pressed.
 *
 * The API accepts a rejection with no comment; **this UI does not**. Work that comes back
 * with no reason attached is precisely the failure the endpoint's own comment support
 * exists to prevent — the annotator learns that it was refused and has to guess what to
 * change. The asymmetry is deliberate rather than an oversight: a script that has already
 * said why elsewhere keeps the option, a person clicking a button in the editor does not.
 *
 * Accepting has no such requirement. "This is fine" needs no explanation.
 */
export function canReject(comment: string): boolean {
  return comment.trim().length > 0;
}

/**
 * What the panel says once the server has answered, read from the state it came back in
 * rather than from what was asked for — if the two ever disagree, the server is right.
 */
export function describeReviewOutcome(state: JobState): string {
  switch (state) {
    case 'accepted':
      return 'Accepted. The job is finished and counts towards the task’s progress.';
    case 'rejected':
      return 'Sent back for changes. Your comment is now an issue on the job, so the ' +
        'annotator can see why.';
    default:
      // The endpoint only ever returns accepted or rejected, so this is a server that has
      // changed under us rather than a case worth wording carefully.
      return `The job is now ${state.replace('_', ' ')}.`;
  }
}
