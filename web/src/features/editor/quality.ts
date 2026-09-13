/**
 * Reading a quality report.
 *
 * The server measures; this decides what a reviewer is shown and in what order. It is
 * separated from the panel because the ordering *is* the feature — a list of 3,000
 * conflicts in arrival order is data, not an answer — and because the decisions below are
 * worth testing without a DOM.
 *
 * Three of them are judgement rather than formatting:
 *
 * **Worst label first.** A reviewer scanning a report wants to know where the work went
 * wrong, and a schema of twelve labels sorted alphabetically buries that. Rows are ordered
 * by F1 ascending, ties broken by name so the order is stable between renders.
 *
 * **A label the schema does not know still gets a row.** A report can name a label that has
 * since been deleted from the project. Dropping the row would quietly subtract its
 * conflicts from what the reviewer sees, and the missing objects are exactly the ones
 * nobody is looking at. It is shown, marked as no longer in the schema.
 *
 * **Truncation is stated, never silent.** `conflictList` caps how many rows it returns and
 * reports the total, so the panel can say "200 of 3,412" rather than implying the job has
 * 200 problems.
 */

import type { LabelStyle } from '@/canvas/types';
import type { ConflictKind, QualityConflict, QualityLabelScore, QualityReport } from '@/api/types';

/** Conflict kinds in the order a reviewer works through them. */
export const CONFLICT_KINDS: ConflictKind[] = ['missing', 'extra', 'wrong_label', 'poor_overlap'];

/** What each kind means, in the words a reviewer needs rather than the enum's. */
export const CONFLICT_LABELS: Record<ConflictKind, string> = {
  missing: 'Missed',
  extra: 'Not in ground truth',
  wrong_label: 'Wrong label',
  poor_overlap: 'Loose geometry',
};

const FALLBACK_COLOR = '#64748b';

/** How many conflicts the panel renders before it starts counting instead. */
export const CONFLICT_LIMIT = 200;

export interface LabelRow extends QualityLabelScore {
  labelId: string;
  name: string;
  color: string;
  /** True when the report names a label the project's schema no longer has. */
  unknown: boolean;
}

export interface ConflictRow {
  conflict: QualityConflict;
  /** The annotation's label, resolved for display. */
  name: string;
  color: string;
  /** The ground truth's label on a `wrong_label` conflict, else null. */
  expected: string | null;
}

export interface ConflictList {
  rows: ConflictRow[];
  /** How many conflicts matched the filter, before the cap. */
  total: number;
  /** True when `rows` is shorter than `total`. */
  truncated: boolean;
}

/**
 * Whether a report still describes the job in front of you.
 *
 * `unknown` is a real answer, not a failure: a report written before the server recorded
 * the version genuinely cannot say, and calling that "current" would be the lie the column
 * was added to prevent.
 */
export type Freshness = 'current' | 'stale' | 'unknown';

export function freshness(
  report: Pick<QualityReport, 'annotation_version'> | null | undefined,
  job: { annotation_version: number } | null | undefined,
): Freshness {
  if (!report || !job) return 'unknown';
  if (report.annotation_version === null || report.annotation_version === undefined) {
    return 'unknown';
  }
  return report.annotation_version === job.annotation_version ? 'current' : 'stale';
}

function conflictsOf(report: QualityReport | null | undefined): QualityConflict[] {
  const conflicts = report?.details?.conflicts;
  return Array.isArray(conflicts) ? conflicts : [];
}

/** How many conflicts of each kind, including the kinds with none. */
export function conflictCounts(report: QualityReport | null | undefined): Record<ConflictKind, number> {
  const counts: Record<ConflictKind, number> = {
    missing: 0,
    extra: 0,
    wrong_label: 0,
    poor_overlap: 0,
  };
  for (const conflict of conflictsOf(report)) {
    if (conflict.kind in counts) counts[conflict.kind] += 1;
  }
  return counts;
}

function resolve(labels: LabelStyle[], labelId: string | null | undefined) {
  const found = labels.find((label) => label.id === labelId);
  if (found) return { name: found.name, color: found.color, unknown: false };
  if (!labelId) return { name: 'unlabelled', color: FALLBACK_COLOR, unknown: true };
  // Enough of the id to tell two deleted labels apart, without a 36-character row.
  return { name: `deleted label ${labelId.slice(0, 8)}`, color: FALLBACK_COLOR, unknown: true };
}

/**
 * Per-label scores, worst first.
 *
 * The report's `per_label` is keyed by label id and is the only place the breakdown lives;
 * a label with no conflicts and no matches never appears there, which is correct — there
 * was nothing to score.
 */
export function labelRows(
  report: QualityReport | null | undefined,
  labels: LabelStyle[],
): LabelRow[] {
  const perLabel = report?.details?.per_label;
  if (!perLabel) return [];

  const rows: LabelRow[] = [];
  for (const [labelId, score] of Object.entries(perLabel)) {
    if (!score || typeof score !== 'object') continue;
    const { name, color, unknown } = resolve(labels, labelId);
    rows.push({
      labelId,
      name,
      color,
      unknown,
      matched: score.matched ?? 0,
      missing: score.missing ?? 0,
      extra: score.extra ?? 0,
      precision: score.precision ?? 0,
      recall: score.recall ?? 0,
      f1: score.f1 ?? 0,
      mean_iou: score.mean_iou ?? 0,
    });
  }
  return rows.sort((a, b) => a.f1 - b.f1 || a.name.localeCompare(b.name));
}

/**
 * Conflicts to show, in frame order, optionally narrowed to one kind.
 *
 * Frame order rather than severity order because the reviewer navigates by frame: working
 * down the list should walk forwards through the video rather than jumping about. Within a
 * frame, the kind order in `CONFLICT_KINDS` keeps two runs of the same report identical.
 */
export function conflictList(
  report: QualityReport | null | undefined,
  options: { kind?: ConflictKind | 'all'; limit?: number; labels?: LabelStyle[] } = {},
): ConflictList {
  const { kind = 'all', limit = CONFLICT_LIMIT, labels = [] } = options;
  const matching = conflictsOf(report).filter(
    (conflict) => kind === 'all' || conflict.kind === kind,
  );

  const ordered = [...matching].sort(
    (a, b) =>
      a.frame - b.frame ||
      CONFLICT_KINDS.indexOf(a.kind) - CONFLICT_KINDS.indexOf(b.kind) ||
      (a.shape_id ?? a.ground_truth_shape_id ?? '').localeCompare(
        b.shape_id ?? b.ground_truth_shape_id ?? '',
      ),
  );

  const rows = ordered.slice(0, Math.max(0, limit)).map((conflict) => {
    // `label_id` is the annotated shape's label and is null on a miss, where nothing was
    // annotated; the ground truth's side is always in `expected_label_id`. Falling back to
    // it is what makes a miss read "Missed: car" rather than "Missed: unlabelled", which
    // would leave the most important kind of conflict the least readable one.
    const primary = conflict.label_id ?? conflict.expected_label_id;
    const { name, color } = resolve(labels, primary);
    // Show the ground truth's label only where it says something the primary does not —
    // a wrong label, or a loose box that is also mislabelled. On a miss the two are the
    // same, and "car → car" is noise.
    const differs =
      Boolean(conflict.expected_label_id) && conflict.expected_label_id !== primary;
    return {
      conflict,
      name,
      color,
      expected: differs ? resolve(labels, conflict.expected_label_id).name : null,
    };
  });

  return { rows, total: matching.length, truncated: rows.length < matching.length };
}

/** A score as a percentage, for a compact readout. Not rounded up to 100 from 0.999. */
export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const clamped = Math.min(1, Math.max(0, value));
  const scaled = Math.floor(clamped * 1000) / 10;
  return `${scaled % 1 === 0 ? scaled.toFixed(0) : scaled.toFixed(1)}%`;
}

/**
 * The colour band for a score.
 *
 * The thresholds are a display convention and nothing else reads them: what counts as an
 * acceptable F1 depends entirely on the task, so this shades a number rather than passing
 * or failing a job.
 */
export function scoreTone(value: number): 'good' | 'fair' | 'poor' {
  if (value >= 0.9) return 'good';
  if (value >= 0.7) return 'fair';
  return 'poor';
}

/** The one-line summary at the top of the panel. */
export function summarise(report: QualityReport | null | undefined): string {
  if (!report) return 'Not scored yet';
  const details = report.details ?? {};
  const frames = details.compared_frames ?? 0;
  const matched = details.matched ?? 0;
  const missing = details.missing ?? 0;
  const extra = details.extra ?? 0;
  const frameWord = frames === 1 ? 'frame' : 'frames';
  return `${matched} matched, ${missing} missed, ${extra} extra over ${frames} ${frameWord}`;
}
