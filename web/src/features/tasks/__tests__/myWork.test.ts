/**
 * The reviewer's queue: what it separates, and what it says when there is nothing to do.
 *
 * The distinction with teeth is between "nothing is named to you" and "nothing is ready
 * yet": a reviewer holding four half-drawn jobs is not idle, and a heading that counts
 * them as waiting would send them looking for work that does not exist.
 */

import { describe, expect, it } from 'vitest';
import type { JobListing, JobState } from '@/api/types';
import { describeJobLocation, partitionReviewQueue, summariseReviewQueue } from '../myWork';

function job(id: string, state: JobState, index = 0, taskName = 'Street scenes'): JobListing {
  return {
    id,
    task_id: 'task-1',
    task_name: taskName,
    project_id: 'project-1',
    index,
    kind: 'annotation',
    state,
    start_frame: 0,
    stop_frame: 9,
    annotation_version: 1,
    shape_count: 0,
    track_count: 0,
    tag_count: 0,
    locked: false,
    created_at: '2026-09-21T00:00:00Z',
    updated_at: '2026-09-21T00:00:00Z',
    assignee: null,
    reviewer: null,
  };
}

describe('partitionReviewQueue', () => {
  it('puts only submitted work in the half that can be acted on', () => {
    const queue = partitionReviewQueue([
      job('a', 'submitted'),
      job('b', 'in_progress'),
      job('c', 'submitted'),
      job('d', 'accepted'),
    ]);
    expect(queue.waiting.map((entry) => entry.id)).toEqual(['a', 'c']);
    expect(queue.later.map((entry) => entry.id)).toEqual(['b', 'd']);
  });

  it('keeps an already-ruled-on job visible rather than dropping it', () => {
    // Dropping them would make a reviewer's own history disappear from the one page that
    // is meant to tell them what they hold.
    const queue = partitionReviewQueue([job('a', 'accepted'), job('b', 'rejected')]);
    expect(queue.waiting).toEqual([]);
    expect(queue.later).toHaveLength(2);
  });

  it('preserves the server ordering within each half', () => {
    const queue = partitionReviewQueue([
      job('newest', 'submitted'),
      job('older', 'submitted'),
      job('oldest', 'submitted'),
    ]);
    expect(queue.waiting.map((entry) => entry.id)).toEqual(['newest', 'older', 'oldest']);
  });

  it('handles an empty queue without inventing a half', () => {
    expect(partitionReviewQueue([])).toEqual({ waiting: [], later: [] });
  });
});

describe('summariseReviewQueue', () => {
  it('counts only what can be reviewed today', () => {
    const queue = partitionReviewQueue([job('a', 'submitted'), job('b', 'in_progress')]);
    expect(summariseReviewQueue(queue)).toBe('1 job waiting on you.');
  });

  it('pluralises', () => {
    const queue = partitionReviewQueue([job('a', 'submitted'), job('b', 'submitted')]);
    expect(summariseReviewQueue(queue)).toBe('2 jobs waiting on you.');
  });

  it('distinguishes "nothing named to you" from "nothing ready yet"', () => {
    expect(summariseReviewQueue({ waiting: [], later: [] })).toBe(
      'Nothing is named to you for review.',
    );
    const holding = partitionReviewQueue([job('a', 'in_progress'), job('b', 'new')]);
    expect(summariseReviewQueue(holding)).toContain('2 jobs named to you');
  });
});

describe('describeJobLocation', () => {
  it('leads with the task, because the list spans every project', () => {
    expect(describeJobLocation(job('a', 'submitted', 1, 'Warehouse aisle 4'))).toBe(
      'Warehouse aisle 4 · job 2',
    );
  });

  it('numbers jobs from one, the way the task page does', () => {
    expect(describeJobLocation(job('a', 'submitted', 0))).toBe('Street scenes · job 1');
  });
});
