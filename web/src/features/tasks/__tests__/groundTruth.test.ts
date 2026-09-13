/**
 * Declaring what "correct" means for a task.
 *
 * The test that carries the most weight here is the blank-field one. Both frame bounds are
 * optional on the API and default to the whole task, so a form that read an empty field as
 * `0` would turn "check everything" into a **one-frame answer key** — and every score the
 * task produced afterwards would be a real-looking number computed from one frame. That is
 * the shape of failure this module exists to prevent, so it is asserted from both ends:
 * blank means the whole task, and `0` typed deliberately still means frame 0.
 */

import { describe, expect, it } from 'vitest';

import type { Job, QualityReport } from '@/api/types';
import {
  describeRange,
  latestReportByJob,
  planRange,
  splitJobs,
} from '../groundTruth';

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: 'job-1',
    task_id: 'task-1',
    index: 0,
    kind: 'annotation',
    state: 'new',
    start_frame: 0,
    stop_frame: 99,
    annotation_version: 1,
    shape_count: 0,
    track_count: 0,
    tag_count: 0,
    locked: false,
    created_at: '2026-09-13T00:00:00Z',
    updated_at: '2026-09-13T00:00:00Z',
    assignee: null,
    reviewer: null,
    ...overrides,
  } as Job;
}

function report(overrides: Partial<QualityReport> = {}): QualityReport {
  return {
    id: 'report-1',
    task_id: 'task-1',
    job_id: 'job-1',
    ground_truth_job_id: 'gt',
    iou_threshold: 0.5,
    precision: 1,
    recall: 1,
    f1: 1,
    annotation_version: 1,
    details: {},
    created_at: '2026-09-13T00:00:00Z',
    ...overrides,
  };
}

describe('splitJobs', () => {
  it('separates the answer key from the work it measures', () => {
    const split = splitJobs([
      job({ id: 'a', index: 0 }),
      job({ id: 'b', index: 1 }),
      job({ id: 'gt', index: 2, kind: 'ground_truth' }),
    ]);
    expect(split.groundTruth?.id).toBe('gt');
    expect(split.annotation.map((each) => each.id)).toEqual(['a', 'b']);
  });

  it('says there is no ground truth when there is none', () => {
    const split = splitJobs([job({ id: 'a' })]);
    expect(split.groundTruth).toBeNull();
    expect(split.annotation).toHaveLength(1);
  });

  it('takes the lowest-indexed one, as the server does', () => {
    // The API refuses a second, but if the two sides disagreed about which one counted, the
    // page would describe a different answer key than the one the scores came from.
    const split = splitJobs([
      job({ id: 'late', index: 7, kind: 'ground_truth' }),
      job({ id: 'early', index: 3, kind: 'ground_truth' }),
    ]);
    expect(split.groundTruth?.id).toBe('early');
  });

  it('survives having no jobs at all', () => {
    expect(splitJobs(undefined)).toEqual({ groundTruth: null, annotation: [] });
    expect(splitJobs([])).toEqual({ groundTruth: null, annotation: [] });
  });
});

describe('planRange', () => {
  it('treats both fields blank as the whole task, not as frame 0', () => {
    const plan = planRange({ start: '', stop: '' }, 600);
    expect(plan.wholeTask).toBe(true);
    expect(plan.start).toBeNull();
    expect(plan.stop).toBeNull();
    expect(plan.problem).toBeNull();
  });

  it('still means frame 0 when 0 is typed deliberately', () => {
    // The mirror of the test above: "blank is not 0" must not become "0 is not 0".
    const plan = planRange({ start: '0', stop: '0' }, 600);
    expect(plan.wholeTask).toBe(false);
    expect(plan.start).toBe(0);
    expect(plan.stop).toBe(0);
    expect(plan.frames).toBe(1);
  });

  it('fills in the other end when only one bound is given', () => {
    expect(planRange({ start: '10', stop: '' }, 100)).toMatchObject({ frames: 90 });
    expect(planRange({ start: '', stop: '9' }, 100)).toMatchObject({ frames: 10 });
  });

  it('counts frames inclusively, as the job range is', () => {
    const plan = planRange({ start: '0', stop: '49' }, 600);
    expect(plan.frames).toBe(50);
    expect(plan.coverage).toBeCloseTo(50 / 600, 6);
  });

  it('refuses a stop before a start', () => {
    const plan = planRange({ start: '40', stop: '10' }, 600);
    expect(plan.problem).toMatch(/comes before/);
  });

  it('refuses a bound past the end of the task', () => {
    const plan = planRange({ start: '0', stop: '600' }, 600);
    expect(plan.problem).toMatch(/ends at frame 599/);
  });

  it('refuses anything that is not a whole number', () => {
    for (const stop of ['4.5', '-2', 'ten', '1e3']) {
      expect(planRange({ start: '0', stop }, 600).problem).toMatch(/whole numbers/);
    }
  });

  it('says so when the task has no frames rather than offering a range', () => {
    expect(planRange({ start: '', stop: '' }, 0).problem).toMatch(/no frames yet/);
  });
});

describe('describeRange', () => {
  it('says how much of the task is being checked, because a count alone does not', () => {
    // 50 frames is a thorough sample of 60 and a thin one of 6,000.
    const text = describeRange(planRange({ start: '0', stop: '49' }, 6000), 6000);
    expect(text).toContain('Frames 0–49');
    expect(text).toContain('50 of 6,000');
    expect(text).toContain('1%');
  });

  it('warns that work outside the range is not measured', () => {
    const text = describeRange(planRange({ start: '10', stop: '20' }, 100), 100);
    expect(text).toMatch(/not measured/);
  });

  it('describes the whole task without inventing a range', () => {
    const text = describeRange(planRange({ start: '', stop: '' }, 600), 600);
    expect(text).toContain('Every frame');
    expect(text).toContain('600');
  });

  it('says nothing when the range is not usable', () => {
    expect(describeRange(planRange({ start: '9', stop: '2' }, 100), 100)).toBe('');
  });
});

describe('latestReportByJob', () => {
  it('keys reports by the job they describe', () => {
    const byJob = latestReportByJob([
      report({ id: 'r1', job_id: 'a', f1: 0.4 }),
      report({ id: 'r2', job_id: 'b', f1: 0.9 }),
    ]);
    expect(byJob.get('a')?.f1).toBe(0.4);
    expect(byJob.get('b')?.f1).toBe(0.9);
  });

  it('keeps the newest when a job has more than one', () => {
    const byJob = latestReportByJob([
      report({ id: 'old', job_id: 'a', created_at: '2026-09-01T00:00:00Z', f1: 0.2 }),
      report({ id: 'new', job_id: 'a', created_at: '2026-09-12T00:00:00Z', f1: 0.8 }),
    ]);
    expect(byJob.get('a')?.id).toBe('new');
  });

  it('keeps the newest regardless of the order they arrive in', () => {
    const byJob = latestReportByJob([
      report({ id: 'new', job_id: 'a', created_at: '2026-09-12T00:00:00Z' }),
      report({ id: 'old', job_id: 'a', created_at: '2026-09-01T00:00:00Z' }),
    ]);
    expect(byJob.get('a')?.id).toBe('new');
  });

  it('ignores a report with no job', () => {
    expect(latestReportByJob([report({ job_id: null })]).size).toBe(0);
  });

  it('survives no reports at all', () => {
    expect(latestReportByJob(undefined).size).toBe(0);
  });
});
