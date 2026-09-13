/**
 * Declaring what "correct" means for a task.
 *
 * A task can hold one ground-truth job — the answer key a reviewer annotates as carefully as
 * they can, and every other job on the task is scored against it. The endpoint has existed
 * since the quality module landed; until now only the API and the CLI could call it.
 *
 * The decisions here are the ones worth testing without a DOM:
 *
 * **A blank frame bound means "the whole task", not frame 0.** Both bounds are optional on
 * the API and default to the full range. A form that reads an empty field as `0` would
 * silently create a one-frame answer key out of a reviewer who meant "check everything" —
 * and the score it produced would look like a real measurement.
 *
 * **The reviewer is told what they are about to commit to** — how many frames, and what
 * share of the task. "Frames 0–49" means something very different on a 60-frame task than
 * on a 6,000-frame one, and only one of those is a sample worth trusting.
 *
 * **The server keeps the authoritative checks.** This validates what a form needs to give
 * an immediate answer (a stop before a start, a bound outside the task); it does not
 * reimplement the server's rules, so the two cannot drift into disagreeing.
 */

import type { Job, QualityReport } from '@/api/types';

export interface JobSplit {
  /** The task's answer key, if it has one. */
  groundTruth: Job | null;
  /** Everything that gets measured against it. */
  annotation: Job[];
}

/**
 * Separate the answer key from the work.
 *
 * The task page listed every job identically, so a ground-truth job read as "Job #5" among
 * the others — and the one job nobody should be assigned ordinary annotation work on looked
 * exactly like the ones they should.
 *
 * Takes the lowest-indexed ground-truth job, matching `find_ground_truth_job` server-side.
 * There should only ever be one — the API refuses a second — but if somehow there were, both
 * sides must agree on which one counts.
 */
export function splitJobs(jobs: Job[] | undefined | null): JobSplit {
  const all = jobs ?? [];
  const truth = all
    .filter((job) => job.kind === 'ground_truth')
    .sort((a, b) => a.index - b.index);
  return {
    groundTruth: truth[0] ?? null,
    annotation: all.filter((job) => job.kind !== 'ground_truth'),
  };
}

export interface RangePlan {
  /** What will be sent, or null for "let the server use the whole task". */
  start: number | null;
  stop: number | null;
  /** How many frames the answer key would cover. */
  frames: number;
  /** That, as a share of the task. 1 when it covers everything. */
  coverage: number;
  /** Why this cannot be submitted, or null when it can. */
  problem: string | null;
  /** True when neither bound was given, so the whole task is the answer key. */
  wholeTask: boolean;
}

function parseBound(value: string): number | null | 'invalid' {
  const trimmed = value.trim();
  if (trimmed === '') return null;
  if (!/^\d+$/.test(trimmed)) return 'invalid';
  return Number(trimmed);
}

/**
 * What a pair of typed frame bounds would produce.
 *
 * Returns a `problem` rather than throwing, because the form shows it below the field while
 * the reviewer is still typing.
 */
export function planRange(
  input: { start: string; stop: string },
  frameCount: number,
): RangePlan {
  const last = frameCount - 1;
  const empty: RangePlan = {
    start: null,
    stop: null,
    frames: 0,
    coverage: 0,
    problem: null,
    wholeTask: true,
  };

  if (frameCount <= 0) {
    return { ...empty, problem: 'This task has no frames yet, so there is nothing to check.' };
  }

  const start = parseBound(input.start);
  const stop = parseBound(input.stop);
  if (start === 'invalid' || stop === 'invalid') {
    return { ...empty, problem: 'Frame numbers are whole numbers, or blank for the whole task.' };
  }

  // Blank means "the whole task" — the API's own default — so an unfilled form is the safe
  // thing rather than a one-frame answer key.
  const from = start ?? 0;
  const to = stop ?? last;
  const wholeTask = start === null && stop === null;

  if (to < from) {
    return {
      ...empty,
      start,
      stop,
      wholeTask,
      problem: `Frame ${to} comes before frame ${from}.`,
    };
  }
  if (to > last) {
    return {
      ...empty,
      start,
      stop,
      wholeTask,
      problem: `This task ends at frame ${last}.`,
    };
  }

  const frames = to - from + 1;
  return {
    start,
    stop,
    frames,
    coverage: frames / frameCount,
    problem: null,
    wholeTask,
  };
}

/**
 * Each job's most recent report, keyed by job id.
 *
 * `GET /tasks/{id}/quality` returns reports newest first, and the server replaces a job's
 * report when it is re-scored — but a task whose jobs were scored at different times returns
 * them interleaved, so "first match wins" is what makes a row show the current score rather
 * than whichever one happened to be at the top.
 */
export function latestReportByJob(
  reports: QualityReport[] | undefined | null,
): Map<string, QualityReport> {
  const latest = new Map<string, QualityReport>();
  for (const report of reports ?? []) {
    if (!report.job_id) continue;
    const existing = latest.get(report.job_id);
    if (!existing || report.created_at > existing.created_at) latest.set(report.job_id, report);
  }
  return latest;
}

/** How a range reads in the panel, once it is valid. */
export function describeRange(plan: RangePlan, frameCount: number): string {
  if (plan.problem || frameCount <= 0) return '';
  if (plan.wholeTask) {
    return `Every frame — all ${frameCount.toLocaleString()} of them — will be the answer key.`;
  }
  const from = plan.start ?? 0;
  const to = plan.stop ?? frameCount - 1;
  const share = Math.round(plan.coverage * 100);
  return (
    `Frames ${from}–${to}: ${plan.frames.toLocaleString()} of ` +
    `${frameCount.toLocaleString()} frames (${share}%). ` +
    'Only these frames are scored — work outside them is not measured.'
  );
}
