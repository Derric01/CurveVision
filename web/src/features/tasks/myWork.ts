/**
 * What the signed-in person has to do, across every project.
 *
 * `GET /jobs?mine=true` has existed since early in the project and filters on
 * `assignee_id` alone, so "what is waiting for *me* to review" could not be asked at all:
 * a named reviewer found submitted work by opening each task and reading its job list.
 * `reviewing=true` is the other half of that question, and this module is the rules for
 * presenting the answer — pure, so they can be pinned without a DOM.
 */

import type { JobListing } from '@/api/types';
import { isReviewable } from '@/features/editor/review';

/**
 * A reviewer's queue, split by whether anything can be done about a job today.
 *
 * Both halves are worth showing. `waiting` is the work: only a **submitted** job can be
 * reviewed, which is the server's rule and `isReviewable`'s, imported rather than restated
 * so the two cannot drift. `later` is everything else named to this reviewer — still being
 * drawn, or already ruled on — which is what stops an empty queue reading as "nobody has
 * given you anything" when in fact three jobs are half-finished.
 */
export interface ReviewQueue {
  waiting: JobListing[];
  later: JobListing[];
}

export function partitionReviewQueue(jobs: readonly JobListing[]): ReviewQueue {
  const waiting: JobListing[] = [];
  const later: JobListing[] = [];
  for (const job of jobs) {
    (isReviewable(job.state) ? waiting : later).push(job);
  }
  return { waiting, later };
}

/**
 * How a job identifies itself in a list that spans every project.
 *
 * "Job #2" is how the server thinks of it and tells a person nothing: the queue is sorted
 * by when the job was created, so two adjacent rows are routinely from different projects.
 * The task's name is what makes a row recognisable, and `GET /jobs` has eagerly loaded it
 * since it was written without ever putting it in the response.
 */
export function describeJobLocation(job: JobListing): string {
  return `${job.task_name} · job ${job.index + 1}`;
}

/**
 * The line above the queue, which has to be right for zero as well as for many.
 *
 * Counting only what can be acted on: a reviewer holding four jobs of which none is
 * submitted has nothing to do, and a heading that says "4" would be telling them otherwise.
 */
export function summariseReviewQueue(queue: ReviewQueue): string {
  const { waiting, later } = queue;
  if (waiting.length === 0) {
    return later.length === 0
      ? 'Nothing is named to you for review.'
      : `Nothing to review yet — ${later.length} job${later.length === 1 ? '' : 's'} named to you ` +
        'when the annotator submits.';
  }
  return `${waiting.length} job${waiting.length === 1 ? '' : 's'} waiting on you.`;
}
