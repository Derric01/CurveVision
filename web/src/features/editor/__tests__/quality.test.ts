/**
 * Reading a quality report.
 *
 * Two things here are worth more than the formatting they look like.
 *
 * **`freshness` decides whether a reviewer trusts the number.** A report is a statement
 * about annotations as they were; if the annotator has since fixed everything, the old F1
 * is still on screen and still wrong. The tests pin all three answers, including the
 * `unknown` one — a report written before the server recorded the version cannot say, and
 * calling that "current" is precisely the failure the column was added to prevent.
 *
 * **`labelRows` must not silently drop a label the schema has forgotten.** A deleted label
 * still has conflicts attached to it, and those are objects nobody is looking at. The test
 * asserts the row survives rather than asserting it is absent.
 */

import { describe, expect, it } from 'vitest';

import type { LabelStyle } from '@/canvas/types';
import type { QualityConflict, QualityReport } from '@/api/types';
import {
  CONFLICT_KINDS,
  CONFLICT_LABELS,
  conflictCounts,
  conflictList,
  freshness,
  labelRows,
  percent,
  scoreTone,
  summarise,
} from '../quality';

const CAR = '11111111-1111-1111-1111-111111111111';
const PERSON = '22222222-2222-2222-2222-222222222222';
const DELETED = '33333333-3333-3333-3333-333333333333';

const LABELS: LabelStyle[] = [
  { id: CAR, name: 'car', color: '#ff0000', visible: true, locked: false },
  { id: PERSON, name: 'person', color: '#00ff00', visible: true, locked: false },
];

function conflict(overrides: Partial<QualityConflict> = {}): QualityConflict {
  return {
    kind: 'missing',
    frame: 0,
    label_id: null,
    expected_label_id: CAR,
    shape_id: null,
    ground_truth_shape_id: 'gt-1',
    iou: null,
    ...overrides,
  };
}

/** The first element, asserted to exist — a missing row should fail the test, not throw. */
function first<T>(items: T[]): T {
  expect(items.length).toBeGreaterThan(0);
  return items[0] as T;
}

function report(overrides: Partial<QualityReport> = {}): QualityReport {
  return {
    id: 'report-1',
    task_id: 'task-1',
    job_id: 'job-1',
    ground_truth_job_id: 'gt-job',
    iou_threshold: 0.5,
    precision: 0.8,
    recall: 0.6,
    f1: 0.686,
    annotation_version: 4,
    details: {
      compared_frames: 10,
      matched: 6,
      missing: 4,
      extra: 1,
      mean_iou: 0.82,
      per_label: {},
      conflicts: [],
    },
    created_at: '2026-09-13T00:00:00Z',
    ...overrides,
  };
}

describe('freshness', () => {
  it('says current when the job has not changed since it was scored', () => {
    expect(freshness(report({ annotation_version: 4 }), { annotation_version: 4 })).toBe('current');
  });

  it('says stale when the job has moved on', () => {
    expect(freshness(report({ annotation_version: 4 }), { annotation_version: 7 })).toBe('stale');
  });

  it('says stale even when the job went backwards, because either way it is a different job', () => {
    expect(freshness(report({ annotation_version: 9 }), { annotation_version: 4 })).toBe('stale');
  });

  it('says unknown for a report written before the version was recorded', () => {
    // Not "current". A report that cannot say which annotations it scored must not be
    // presented as describing the ones on screen.
    expect(freshness(report({ annotation_version: null }), { annotation_version: 4 })).toBe(
      'unknown',
    );
  });

  it('says unknown when there is no report or no job yet', () => {
    expect(freshness(null, { annotation_version: 1 })).toBe('unknown');
    expect(freshness(report(), null)).toBe('unknown');
  });
});

describe('labelRows', () => {
  const scored = report({
    details: {
      per_label: {
        [CAR]: {
          matched: 9,
          missing: 1,
          extra: 0,
          precision: 1,
          recall: 0.9,
          f1: 0.947,
          mean_iou: 0.88,
        },
        [PERSON]: {
          matched: 2,
          missing: 6,
          extra: 3,
          precision: 0.4,
          recall: 0.25,
          f1: 0.308,
          mean_iou: 0.61,
        },
      },
    },
  });

  it('puts the worst label first, because that is what the reviewer is looking for', () => {
    expect(labelRows(scored, LABELS).map((row) => row.name)).toEqual(['person', 'car']);
  });

  it('resolves each label to its name and colour', () => {
    const worst = first(labelRows(scored, LABELS));
    expect(worst).toMatchObject({ labelId: PERSON, name: 'person', color: '#00ff00', f1: 0.308 });
  });

  it('keeps a label the schema no longer has, rather than hiding its conflicts', () => {
    const withDeleted = report({
      details: {
        per_label: {
          [DELETED]: {
            matched: 0,
            missing: 5,
            extra: 0,
            precision: 0,
            recall: 0,
            f1: 0,
            mean_iou: 0,
          },
        },
      },
    });
    const rows = labelRows(withDeleted, LABELS);
    expect(rows).toHaveLength(1);
    const row = first(rows);
    expect(row.unknown).toBe(true);
    expect(row.missing).toBe(5);
    // Identifiable: two deleted labels must not both read as "deleted label".
    expect(row.name).toContain(DELETED.slice(0, 8));
  });

  it('breaks an F1 tie by name so two renders agree', () => {
    const tied = report({
      details: {
        per_label: {
          [PERSON]: { matched: 1, missing: 1, extra: 1, precision: 0.5, recall: 0.5, f1: 0.5, mean_iou: 0.7 },
          [CAR]: { matched: 1, missing: 1, extra: 1, precision: 0.5, recall: 0.5, f1: 0.5, mean_iou: 0.7 },
        },
      },
    });
    expect(labelRows(tied, LABELS).map((row) => row.name)).toEqual(['car', 'person']);
  });

  it('returns nothing for a report with no per-label breakdown', () => {
    expect(labelRows(report({ details: {} }), LABELS)).toEqual([]);
    expect(labelRows(null, LABELS)).toEqual([]);
  });
});

describe('conflictList', () => {
  const conflicts: QualityConflict[] = [
    conflict({ kind: 'extra', frame: 7, label_id: PERSON, expected_label_id: null, shape_id: 's7' }),
    conflict({ kind: 'missing', frame: 2, expected_label_id: CAR }),
    conflict({
      kind: 'wrong_label',
      frame: 2,
      label_id: PERSON,
      expected_label_id: CAR,
      shape_id: 's2',
      iou: 0.91,
    }),
    conflict({
      kind: 'poor_overlap',
      frame: 4,
      label_id: CAR,
      expected_label_id: CAR,
      shape_id: 's4',
      iou: 0.31,
    }),
    // Geometry pairs candidates, not labels, so a loose box can also be mislabelled.
    conflict({
      kind: 'poor_overlap',
      frame: 9,
      label_id: PERSON,
      expected_label_id: CAR,
      shape_id: 's9',
      iou: 0.33,
    }),
  ];
  const withConflicts = report({ details: { conflicts } });

  it('walks forwards through the video rather than in arrival order', () => {
    expect(conflictList(withConflicts, { labels: LABELS }).rows.map((row) => row.conflict.frame)).toEqual(
      [2, 2, 4, 7, 9],
    );
  });

  it('orders two conflicts on the same frame by kind, identically every run', () => {
    const kinds = conflictList(withConflicts, { labels: LABELS })
      .rows.filter((row) => row.conflict.frame === 2)
      .map((row) => row.conflict.kind);
    expect(kinds).toEqual(['missing', 'wrong_label']);
  });

  it('names a missed object by the label the ground truth gave it', () => {
    // Nothing was annotated, so `label_id` is null; reading only that would print
    // "unlabelled" on every miss and make the most important kind the least readable.
    // This is the defect the browser harness caught, not the unit suite.
    const missed = first(conflictList(withConflicts, { kind: 'missing', labels: LABELS }).rows);
    expect(missed.name).toBe('car');
    expect(missed.color).toBe('#ff0000');
  });

  it('shows both sides of a wrong label', () => {
    const wrong = first(conflictList(withConflicts, { kind: 'wrong_label', labels: LABELS }).rows);
    expect(wrong.name).toBe('person');
    expect(wrong.expected).toBe('car');
  });

  it('shows the ground truth label on a loose box that is also mislabelled', () => {
    const rows = conflictList(withConflicts, { kind: 'poor_overlap', labels: LABELS }).rows;
    const mislabelled = rows.find((row) => row.conflict.frame === 9);
    expect(mislabelled?.name).toBe('person');
    expect(mislabelled?.expected).toBe('car');
  });

  it('stays quiet when the two sides agree, rather than printing "car -> car"', () => {
    const rows = conflictList(withConflicts, { labels: LABELS });
    const miss = rows.rows.find((row) => row.conflict.kind === 'missing');
    const tight = rows.rows.find(
      (row) => row.conflict.kind === 'poor_overlap' && row.conflict.frame === 4,
    );
    const extra = rows.rows.find((row) => row.conflict.kind === 'extra');
    expect(miss?.expected).toBeNull();
    expect(tight?.expected).toBeNull();
    expect(extra?.expected).toBeNull();
  });

  it('narrows to one kind', () => {
    const only = conflictList(withConflicts, { kind: 'extra', labels: LABELS });
    expect(only.total).toBe(1);
    expect(first(only.rows).conflict.kind).toBe('extra');
  });

  it('reports the true total when it truncates, so 200 rows never read as 200 problems', () => {
    const many = report({
      details: {
        conflicts: Array.from({ length: 500 }, (_, index) =>
          conflict({ frame: index, ground_truth_shape_id: `gt-${index}` }),
        ),
      },
    });
    const list = conflictList(many, { limit: 200, labels: LABELS });
    expect(list.rows).toHaveLength(200);
    expect(list.total).toBe(500);
    expect(list.truncated).toBe(true);
  });

  it('is not truncated when everything fits', () => {
    const list = conflictList(withConflicts, { labels: LABELS });
    expect(list.truncated).toBe(false);
    expect(list.total).toBe(5);
  });

  it('survives a report with no details at all', () => {
    expect(conflictList(report({ details: {} })).rows).toEqual([]);
    expect(conflictList(null).total).toBe(0);
  });

  it('ignores a details blob whose conflicts are not a list', () => {
    // `details` is free-form JSON on the server, so the client cannot assume its shape.
    const malformed = { ...report(), details: { conflicts: 'nope' } } as unknown as QualityReport;
    expect(conflictList(malformed).rows).toEqual([]);
  });
});

describe('conflictCounts', () => {
  it('counts every kind, including the ones with none', () => {
    const counted = conflictCounts(
      report({
        details: {
          conflicts: [
            conflict({ kind: 'missing', frame: 1 }),
            conflict({ kind: 'missing', frame: 2 }),
            conflict({ kind: 'extra', frame: 3 }),
          ],
        },
      }),
    );
    expect(counted).toEqual({ missing: 2, extra: 1, wrong_label: 0, poor_overlap: 0 });
  });

  it('has a readable name for every kind the server can produce', () => {
    for (const kind of CONFLICT_KINDS) {
      expect(CONFLICT_LABELS[kind]).toBeTruthy();
    }
  });
});

describe('percent', () => {
  it('formats a score without inventing precision', () => {
    expect(percent(1)).toBe('100%');
    expect(percent(0)).toBe('0%');
    expect(percent(0.686)).toBe('68.6%');
  });

  it('never rounds a near-miss up to 100%', () => {
    // 99.9% and 100% mean different things to someone deciding whether to accept a job.
    expect(percent(0.9999)).toBe('99.9%');
  });

  it('returns a dash rather than NaN%', () => {
    expect(percent(null)).toBe('—');
    expect(percent(undefined)).toBe('—');
    expect(percent(Number.NaN)).toBe('—');
  });
});

describe('scoreTone', () => {
  it('bands a score', () => {
    expect(scoreTone(0.95)).toBe('good');
    expect(scoreTone(0.9)).toBe('good');
    expect(scoreTone(0.75)).toBe('fair');
    expect(scoreTone(0.69)).toBe('poor');
  });
});

describe('summarise', () => {
  it('says what was compared, not just the score', () => {
    expect(summarise(report())).toBe('6 matched, 4 missed, 1 extra over 10 frames');
  });

  it('agrees with itself on one frame', () => {
    const single = report({ details: { compared_frames: 1, matched: 1, missing: 0, extra: 0 } });
    expect(summarise(single)).toBe('1 matched, 0 missed, 0 extra over 1 frame');
  });

  it('says so when there is no report', () => {
    expect(summarise(null)).toBe('Not scored yet');
  });
});
