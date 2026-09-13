/**
 * The task's answer key.
 *
 * Creating a ground-truth job has been an API or CLI call since the quality module landed,
 * which meant the one decision that gives every quality score on the task its meaning was
 * the one decision you could not make from the application.
 *
 * It belongs here rather than in the editor. Declaring what "correct" means for a task is a
 * task-level decision of the same weight as defining the label schema — which is why the
 * endpoint takes `Action.UPDATE` on the task rather than a review permission — and the
 * editor is where you work on one job, not where you decide the terms for all of them.
 *
 * Two things this panel is careful about:
 *
 * - **It says what a narrow range costs.** Only frames the ground truth covers are scored,
 *   so a 50-frame answer key on a 6,000-frame task measures 0.8% of the work. That is often
 *   the right trade — 50 frames checked properly beats 5,000 checked carelessly — but it
 *   should be a choice rather than a surprise.
 * - **It does not pretend a second one is possible.** The API allows one per task and
 *   refuses another, so once there is one this becomes a description rather than a form.
 */

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { PenLine, Target } from 'lucide-react';

import { api } from '@/api/client';
import type { Job } from '@/api/types';
import { Badge, Button, ErrorNotice, Input, Panel } from '@/ui/primitives';
import { describeRange, planRange } from './groundTruth';

export function GroundTruthPanel({
  taskId,
  frameCount,
  groundTruth,
}: {
  taskId: string;
  frameCount: number;
  groundTruth: Job | null;
}) {
  const queryClient = useQueryClient();
  const [range, setRange] = useState({ start: '', stop: '' });

  const plan = planRange(range, frameCount);

  const create = useMutation({
    mutationFn: () =>
      api.createGroundTruthJob(taskId, {
        // Omitted rather than sent as 0: the API defaults an absent bound to the whole task,
        // and sending a number we invented would make a blank field mean something.
        ...(plan.start !== null ? { start_frame: plan.start } : {}),
        ...(plan.stop !== null ? { stop_frame: plan.stop } : {}),
      }),
    onSuccess: async () => {
      setRange({ start: '', stop: '' });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['jobs', taskId] }),
        queryClient.invalidateQueries({ queryKey: ['task', taskId] }),
      ]);
    },
  });

  if (groundTruth) {
    return (
      <Panel
        title={
          <span className="flex items-center gap-1.5">
            <Target size={13} />
            Ground truth
          </span>
        }
        actions={<Badge tone="accent">the answer key</Badge>}
      >
        <p className="text-sm text-ink-400">
          Job #{groundTruth.index + 1} holds this task&apos;s ground truth, over{' '}
          <span className="font-mono text-ink-300">
            frames {groundTruth.start_frame}–{groundTruth.stop_frame}
          </span>
          . Every other job is scored against it, on those frames only.
        </p>
        <p className="mt-2 text-xs text-ink-500">
          {groundTruth.shape_count} shape{groundTruth.shape_count === 1 ? '' : 's'} ·{' '}
          {groundTruth.track_count} track{groundTruth.track_count === 1 ? '' : 's'}
          {groundTruth.assignee && ` · ${groundTruth.assignee.username}`}
          {groundTruth.shape_count === 0 && groundTruth.track_count === 0 && (
            <span className="ml-1 text-amber-400">
              — nothing annotated yet, so scoring against it would mark every object as
              invented.
            </span>
          )}
        </p>
        <div className="mt-3 flex items-center gap-3">
          <Link
            to={`/jobs/${groundTruth.id}`}
            className="inline-flex items-center gap-1.5 text-sm text-curve-300 hover:text-curve-200"
          >
            <PenLine size={14} />
            Annotate the ground truth
          </Link>
        </div>
        <p className="mt-3 text-xs text-ink-600">
          Only a reviewer, or whoever this job is assigned to, can read its annotations — a
          score an annotator could have copied measures nothing. There is one per task; to
          replace it, delete this job first.
        </p>
      </Panel>
    );
  }

  const blocked = plan.problem !== null || frameCount <= 0;

  return (
    <Panel
      title={
        <span className="flex items-center gap-1.5">
          <Target size={13} />
          Ground truth
        </span>
      }
    >
      <p className="text-sm text-ink-400">
        A ground-truth job is one more job on this task, annotated as carefully as you can,
        that every other job is then measured against — precision, recall and a list of what
        differs, per job and per label.
      </p>

      {/* Sized by the wrapper rather than by `className`, which `Input` puts on the element
          that already carries `w-full`. `name` is what associates each label with its
          field. */}
      <div className="mt-4 flex flex-wrap items-start gap-3">
        <div className="w-36">
          <Input
            name="ground-truth-start"
            label="First frame"
            hint="Blank for the start"
            inputMode="numeric"
            value={range.start}
            onChange={(event) =>
              setRange((current) => ({ ...current, start: event.target.value }))
            }
          />
        </div>
        <div className="w-36">
          <Input
            name="ground-truth-stop"
            label="Last frame"
            hint="Blank for the end"
            inputMode="numeric"
            value={range.stop}
            onChange={(event) => setRange((current) => ({ ...current, stop: event.target.value }))}
          />
        </div>
      </div>

      {plan.problem ? (
        <p className="mt-3 text-xs text-amber-300">{plan.problem}</p>
      ) : (
        <p className="mt-3 text-xs text-ink-500">{describeRange(plan, frameCount)}</p>
      )}

      {create.error !== null && create.error !== undefined && (
        <div className="mt-3">
          <ErrorNotice error={create.error} />
        </div>
      )}

      <div className="mt-4">
        <Button
          variant="primary"
          size="sm"
          onClick={() => create.mutate()}
          disabled={blocked || create.isPending}
        >
          {create.isPending ? 'Creating…' : 'Create the ground-truth job'}
        </Button>
      </div>
    </Panel>
  );
}
