/**
 * The quality report, in the editor.
 *
 * The comparison engine has worked since the quality module landed, but the only way to see
 * a report was the CLI — which means the reviewer who needs it is the one person not
 * looking at a terminal. This panel puts it beside the frames it describes.
 *
 * The design decision that matters: **a conflict is a place, not a statistic.** Every one
 * carries a frame, so every row seeks there. A reviewer reads "missed a car on frame 214",
 * clicks, and is looking at frame 214 with the annotation in front of them. A report that
 * only totals things tells you a job is bad without telling you where.
 *
 * Two honesty rules the panel keeps:
 *
 * - A report whose job has changed since it was computed is **marked stale**, and a report
 *   that cannot say is marked unknown rather than shown as current.
 * - When the conflict list is capped, it says how many there really are.
 */

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ChevronRight, RefreshCw, Target } from 'lucide-react';
import clsx from 'clsx';

import { ApiError, api } from '@/api/client';
import type { ConflictKind, Job } from '@/api/types';
import type { LabelStyle } from '@/canvas/types';
import { Button, Spinner } from '@/ui/primitives';
import {
  CONFLICT_KINDS,
  CONFLICT_LABELS,
  CONFLICT_LIMIT,
  conflictCounts,
  conflictList,
  freshness,
  labelRows,
  percent,
  scoreTone,
  summarise,
} from './quality';

const TONE_CLASS = {
  good: 'text-emerald-400',
  fair: 'text-amber-400',
  poor: 'text-red-400',
} as const;

export function QualityPanel({
  job,
  labels,
  onSeek,
}: {
  job: Job | undefined;
  labels: LabelStyle[];
  onSeek: (frame: number) => void;
}) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<ConflictKind | 'all'>('all');
  const jobId = job?.id ?? '';

  const report = useQuery({
    queryKey: ['quality', jobId],
    enabled: Boolean(jobId) && job?.kind !== 'ground_truth',
    // A job with no report yet 404s, which is the ordinary state and not worth retrying.
    retry: false,
    queryFn: async () => {
      try {
        return await api.jobQuality(jobId);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
  });

  const compute = useMutation({
    mutationFn: () => api.computeQuality(jobId),
    onSuccess: (fresh) => queryClient.setQueryData(['quality', jobId], fresh),
  });

  const current = report.data ?? null;
  const rows = useMemo(() => labelRows(current, labels), [current, labels]);
  const counts = useMemo(() => conflictCounts(current), [current]);
  const list = useMemo(
    () => conflictList(current, { kind, labels, limit: CONFLICT_LIMIT }),
    [current, kind, labels],
  );
  const age = freshness(current, job);

  if (job?.kind === 'ground_truth') {
    return (
      <Section>
        <p className="px-3 pb-3 text-xs text-ink-600">
          This is the ground-truth job. It defines what correct means for the task, so it is
          not scored against itself.
        </p>
      </Section>
    );
  }

  return (
    <Section>
      <div className="px-3 pb-3">
        {report.isLoading ? (
          <div className="flex items-center gap-2 py-2 text-xs text-ink-500">
            <Spinner className="h-3 w-3" /> Loading
          </div>
        ) : (
          <>
            {!current && (
              <p className="pb-2 text-xs text-ink-600">
                Not scored yet. Comparing this job against the task&apos;s ground-truth job
                gives precision, recall and a list of what differs.
              </p>
            )}

            <Button
              size="sm"
              variant="secondary"
              className="w-full"
              onClick={() => compute.mutate()}
              disabled={compute.isPending}
            >
              {compute.isPending ? (
                <Spinner className="mr-1.5 h-3 w-3" />
              ) : (
                <RefreshCw size={12} className="mr-1.5" />
              )}
              {current ? 'Re-check' : 'Check against ground truth'}
            </Button>

            {/* The two refusals a reviewer actually hits — no ground-truth job on the task,
                and not having the reviewer permission — arrive as the server's own message,
                which says which one it is. */}
            {compute.error && (
              <p className="mt-2 rounded border border-red-500/30 bg-red-500/10 px-2 py-1.5 text-[11px] text-red-300">
                {compute.error instanceof Error ? compute.error.message : 'Could not score this job'}
              </p>
            )}
            {report.error && !compute.error && (
              <p className="mt-2 text-[11px] text-ink-600">
                {report.error instanceof Error ? report.error.message : 'Could not load the report'}
              </p>
            )}
          </>
        )}

        {current && (
          <>
            {age !== 'current' && (
              <p
                className="mt-2 flex items-start gap-1.5 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-300"
                role="status"
              >
                <AlertTriangle size={12} className="mt-px shrink-0" />
                {age === 'stale' ? (
                  <span>
                    The job has changed since this was computed. Re-check before acting on it.
                  </span>
                ) : (
                  <span>
                    This report does not record which version of the job it scored, so it may
                    describe older work.
                  </span>
                )}
              </p>
            )}

            <dl className="mt-3 grid grid-cols-3 gap-1 text-center">
              <Score label="Precision" value={current.precision} />
              <Score label="Recall" value={current.recall} />
              <Score label="F1" value={current.f1} />
            </dl>

            <p className="mt-2 text-[11px] text-ink-500">{summarise(current)}</p>
            <p className="text-[11px] text-ink-600">
              IoU ≥ {current.iou_threshold} · mean {percent(current.details?.mean_iou)}
            </p>

            {rows.length > 0 && (
              <table className="mt-3 w-full text-[11px]">
                <thead>
                  <tr className="text-ink-600">
                    <th className="pb-1 text-left font-medium">Label</th>
                    <th className="pb-1 text-right font-medium">P</th>
                    <th className="pb-1 text-right font-medium">R</th>
                    <th className="pb-1 text-right font-medium">F1</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.labelId} className="text-ink-300">
                      <td className="flex min-w-0 items-center gap-1.5 py-0.5">
                        <span
                          className="h-2 w-2 shrink-0 rounded-sm"
                          style={{ backgroundColor: row.color }}
                        />
                        <span className={clsx('truncate', row.unknown && 'italic text-ink-500')}>
                          {row.name}
                        </span>
                      </td>
                      <td className="py-0.5 text-right tabular-nums">{percent(row.precision)}</td>
                      <td className="py-0.5 text-right tabular-nums">{percent(row.recall)}</td>
                      <td
                        className={clsx(
                          'py-0.5 text-right tabular-nums',
                          TONE_CLASS[scoreTone(row.f1)],
                        )}
                      >
                        {percent(row.f1)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            <div className="mt-3 flex flex-wrap gap-1">
              <Chip active={kind === 'all'} onClick={() => setKind('all')}>
                All {conflictTotal(counts)}
              </Chip>
              {CONFLICT_KINDS.filter((each) => counts[each] > 0).map((each) => (
                <Chip key={each} active={kind === each} onClick={() => setKind(each)}>
                  {CONFLICT_LABELS[each]} {counts[each]}
                </Chip>
              ))}
            </div>
          </>
        )}
      </div>

      {current && (
        <ul className="max-h-[26vh] overflow-auto border-t border-ink-800">
          {list.rows.length === 0 ? (
            <li className="px-3 py-3 text-xs text-ink-600">
              {conflictTotal(counts) === 0
                ? 'No conflicts — this job matches the ground truth on every compared frame.'
                : 'None of this kind.'}
            </li>
          ) : (
            list.rows.map((row) => (
              <li key={conflictKey(row.conflict, row.name)}>
                <button
                  type="button"
                  onClick={() => onSeek(row.conflict.frame)}
                  className="flex w-full items-center gap-2 px-3 py-1 text-left text-[11px] text-ink-300 hover:bg-ink-850"
                >
                  <span
                    className="h-2 w-2 shrink-0 rounded-sm"
                    style={{ backgroundColor: row.color }}
                  />
                  <span className="w-10 shrink-0 tabular-nums text-ink-500">
                    {row.conflict.frame}
                  </span>
                  <span className="truncate">
                    {CONFLICT_LABELS[row.conflict.kind]}: {row.name}
                    {row.expected && <span className="text-ink-500"> → {row.expected}</span>}
                  </span>
                  {/* `details` is free-form JSON, so a missing key reaches here as
                      undefined rather than null; both mean "no overlap to report". */}
                  {row.conflict.iou !== null && row.conflict.iou !== undefined && (
                    <span className="ml-auto shrink-0 tabular-nums text-ink-600">
                      {percent(row.conflict.iou)}
                    </span>
                  )}
                  <ChevronRight size={11} className="shrink-0 text-ink-700" />
                </button>
              </li>
            ))
          )}
          {list.truncated && (
            <li className="px-3 py-2 text-[11px] text-ink-600">
              Showing {list.rows.length} of {list.total}. Re-check after fixing these, or narrow
              by kind.
            </li>
          )}
        </ul>
      )}
    </Section>
  );
}

function Section({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex shrink-0 flex-col border-t border-ink-800">
      <h3 className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium uppercase tracking-wide text-ink-500">
        <Target size={12} />
        Quality
      </h3>
      {children}
    </div>
  );
}

function Score({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded bg-ink-900 py-1.5">
      <dt className="text-[10px] uppercase tracking-wide text-ink-600">{label}</dt>
      <dd className={clsx('font-mono text-sm tabular-nums', TONE_CLASS[scoreTone(value)])}>
        {percent(value)}
      </dd>
    </div>
  );
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={clsx(
        'rounded border px-1.5 py-0.5 text-[10px]',
        active
          ? 'border-curve-500/40 bg-curve-500/10 text-curve-200'
          : 'border-ink-700 text-ink-400 hover:text-ink-200',
      )}
    >
      {children}
    </button>
  );
}

function conflictTotal(counts: Record<ConflictKind, number>): number {
  return CONFLICT_KINDS.reduce((sum, kind) => sum + counts[kind], 0);
}

/**
 * A stable key per row.
 *
 * A missed object has no `shape_id` and an extra one has no ground-truth id, so neither
 * alone identifies a row; the frame and kind complete it. Two genuinely identical entries
 * would still collide, which React tolerates here because the rows are not reordered.
 */
function conflictKey(
  conflict: { kind: string; frame: number; shape_id: string | null; ground_truth_shape_id: string | null },
  name: string,
): string {
  return `${conflict.frame}:${conflict.kind}:${conflict.shape_id ?? ''}:${conflict.ground_truth_shape_id ?? ''}:${name}`;
}
