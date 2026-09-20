/**
 * Run a model over this job's frames and keep what it found, for review.
 *
 * Every decision worth arguing about lives in `autoAnnotate.ts` and is tested without a DOM;
 * this is the wiring and the copy.
 *
 * Predictions land as ordinary annotations with `source: "model"`, which the renderer draws
 * dashed — an annotator can tell at a glance what a machine proposed and what a human
 * placed, and can edit or delete any of them like anything else they drew.
 */

import { useCallback, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Sparkles } from 'lucide-react';
import { api } from '@/api/client';
import type { InferenceResult, Label } from '@/api/types';
import { Button, ErrorNotice, Spinner } from '@/ui/primitives';
import {
  blockedReason,
  describePlan,
  describeUnmatched,
  ignoredClassesNote,
  labelMapping,
  plannedClasses,
  summarise,
  unavailableModels,
  usableModels,
} from './autoAnnotate';

export function AutoAnnotatePanel({
  jobId,
  labels,
  onRan,
}: {
  jobId: string;
  /** The project's top-level labels, in schema order. */
  labels: Pick<Label, 'id' | 'name'>[];
  onRan: () => void;
}) {
  const queryClient = useQueryClient();
  // Nice-to-have, like the quality panel: a viewer without the permission gets a 403 and
  // the rest of the editor must still work.
  const models = useQuery({ queryKey: ['models'], queryFn: () => api.models(), retry: false });

  const [modelId, setModelId] = useState<string>('');
  const [classText, setClassText] = useState('');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<InferenceResult | null>(null);
  const [error, setError] = useState<unknown>(null);

  const available = useMemo(() => usableModels(models.data), [models.data]);
  const blocked = useMemo(() => unavailableModels(models.data), [models.data]);
  const selected = available.find((model) => model.id === modelId) ?? available[0];
  const projectLabels = useMemo(() => labels.map((label) => label.name), [labels]);

  const plan = plannedClasses(selected, classText, projectLabels);
  // Only an open-vocabulary model is asked what the box holds, so only it can be blocked
  // over the contents: for a fixed-head model the box is not even on screen, and refusing
  // over text nobody can see or clear is a dead end. `ignoredClassesNote` covers that case.
  const reason = blockedReason(selected, selected?.open_vocabulary ? classText : '', projectLabels);
  const ignored = ignoredClassesNote(selected, classText);
  const { mapping, unmatched } = labelMapping(plan.classes, labels);
  const unmatchedNote = describeUnmatched(unmatched);

  const run = useCallback(async () => {
    if (!selected) return;
    setError(null);
    setResult(null);
    setRunning(true);
    try {
      const response = await api.runInference(jobId, {
        model_id: selected.id,
        job_id: jobId,
        label_mapping: mapping,
        // Only for an open-vocabulary model: sending these to a fixed-head one is a 422,
        // which is the server being honest rather than something to work around.
        ...(selected.open_vocabulary && plan.source === 'typed' ? { classes: plan.classes } : {}),
        persist: true,
      });
      setResult(response);
      // Predictions are annotations, so the frame has to be re-read or they stay invisible
      // until something else happens to invalidate it.
      await queryClient.invalidateQueries({ queryKey: ['annotations', jobId] });
      onRan();
    } catch (caught) {
      setError(caught);
    } finally {
      setRunning(false);
    }
  }, [jobId, mapping, onRan, plan.classes, plan.source, queryClient, selected]);

  return (
    <div className="border-t border-ink-800 px-3 py-2" data-auto-annotate="">
      <h3 className="flex items-center gap-1.5 py-1 text-xs font-medium uppercase tracking-wide text-ink-500">
        <Sparkles size={12} />
        Auto-annotate
      </h3>

      {models.isLoading ? (
        <Spinner className="my-2 h-4 w-4" />
      ) : available.length === 0 ? (
        <p className="py-1 text-xs text-ink-500" data-auto-annotate-empty="">
          No model is registered. CurveVision never bundles model weights — register an
          inference endpoint and it appears here.
          {blocked.length > 0 && (
            <>
              {' '}
              {blocked.map(({ model, reason: why }) => `${model.name} ${why}`).join('; ')}.
            </>
          )}
        </p>
      ) : (
        <>
          <select
            value={selected?.id ?? ''}
            onChange={(event) => {
              setModelId(event.target.value);
              setResult(null);
              setError(null);
            }}
            className="w-full rounded border border-ink-700 bg-ink-900 px-2 py-1 text-xs text-ink-200"
            aria-label="Model"
            data-auto-annotate-model=""
          >
            {available.map((model) => (
              <option key={model.id} value={model.id}>
                {model.name}
              </option>
            ))}
          </select>

          {selected?.open_vocabulary && (
            <input
              type="text"
              value={classText}
              onChange={(event) => setClassText(event.target.value)}
              placeholder={projectLabels.join(', ') || 'forklift, pallet'}
              aria-label="Classes to look for"
              data-auto-annotate-classes=""
              className="mt-1.5 w-full rounded border border-ink-700 bg-ink-900 px-2 py-1 text-xs text-ink-200 placeholder:text-ink-600"
            />
          )}

          <p className="mt-1.5 text-[11px] text-ink-500" data-auto-annotate-plan="">
            {reason ?? describePlan(plan)}
          </p>

          {!reason && ignored && (
            <p className="mt-1 text-[11px] text-amber-300/80" data-auto-annotate-ignored="">
              {ignored}
            </p>
          )}

          {!reason && unmatchedNote && (
            <p className="mt-1 text-[11px] text-amber-300/80" data-auto-annotate-unmatched="">
              {unmatchedNote}
            </p>
          )}

          <Button
            variant="secondary"
            size="sm"
            className="mt-2 w-full"
            onClick={() => void run()}
            disabled={running || reason !== null}
            data-auto-annotate-run=""
          >
            {running ? 'Looking…' : 'Find objects'}
          </Button>

          {result && (
            <p className="mt-1.5 text-[11px] text-ink-400" data-auto-annotate-result="">
              {summarise(result)}
            </p>
          )}
          {result?.warnings.map((warning) => (
            <p key={warning} className="mt-1 text-[11px] text-amber-300/80">
              {warning}
            </p>
          ))}
          {error != null && (
            <div className="mt-2">
              <ErrorNotice error={error} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
