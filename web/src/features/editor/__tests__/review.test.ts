/**
 * The rules behind the reviewer's two buttons.
 *
 * Two of these mirror a server rule rather than inventing one — which states can be
 * reviewed, and which can be submitted — so they are worth pinning here: if the server's
 * `ALLOWED_TRANSITIONS` ever changes, a button that offers an action the API refuses is
 * exactly the kind of silent failure these tests exist to catch.
 *
 * The wiring (that the panel reaches the endpoint, that a rejection's comment becomes an
 * issue the annotator can read) is `scripts/verify_job_review.py`'s job, against the real
 * packaged application.
 */

import { describe, expect, it } from 'vitest';
import type { JobState } from '@/api/types';
import { canReject, canSubmit, describeReviewOutcome, isReviewable } from '../review';

const EVERY_STATE: JobState[] = ['new', 'in_progress', 'submitted', 'accepted', 'rejected'];

describe('isReviewable', () => {
  it('offers the controls only for a submitted job', () => {
    expect(EVERY_STATE.filter(isReviewable)).toEqual(['submitted']);
  });

  it('treats a job that has not loaded yet as not reviewable, rather than flashing buttons', () => {
    expect(isReviewable(undefined)).toBe(false);
  });
});

describe('canSubmit', () => {
  // The server's own ALLOWED_TRANSITIONS: new, in_progress and rejected may become
  // submitted. Accepted may not -- that is the case the old predicate got wrong.
  it('allows exactly the states the server lets become submitted', () => {
    expect(EVERY_STATE.filter(canSubmit)).toEqual(['new', 'in_progress', 'rejected']);
  });

  it('allows a rejected job to be resubmitted, which is the point of sending one back', () => {
    expect(canSubmit('rejected')).toBe(true);
  });

  it('refuses an accepted job, where the server answers 409', () => {
    expect(canSubmit('accepted')).toBe(false);
  });

  it('refuses before the job has loaded', () => {
    expect(canSubmit(undefined)).toBe(false);
  });
});

describe('canReject', () => {
  it('requires a reason, because work that comes back unexplained cannot be acted on', () => {
    expect(canReject('')).toBe(false);
    expect(canReject('the boxes on frames 4-9 are off by a wheel')).toBe(true);
  });

  it('does not accept whitespace as a reason', () => {
    expect(canReject('   \n\t ')).toBe(false);
  });
});

describe('describeReviewOutcome', () => {
  it('says an accepted job is finished', () => {
    expect(describeReviewOutcome('accepted')).toContain('Accepted');
  });

  it('tells the reviewer where their comment went, so they do not repeat it as an issue', () => {
    const said = describeReviewOutcome('rejected');
    expect(said).toContain('Sent back');
    expect(said).toContain('issue');
  });

  it('reads the state back from the server rather than from what was asked for', () => {
    // Not a state the endpoint returns; it exists so a server that changed under us reads
    // as something odd rather than as a confident lie about what happened.
    expect(describeReviewOutcome('in_progress')).toBe('The job is now in progress.');
  });
});
