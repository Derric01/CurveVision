/**
 * The warning shown when a task's frame count has never been verified by decoding.
 *
 * All the decisions worth testing live in `frameCount.ts`; this is the rendering and the
 * one mutation, kept apart so the wording and the edge cases can be tested in a runtime
 * with no DOM.
 */

import { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import type { TaskMediaMeta } from '@/api/types';
import { api } from '@/api/client';
import { Button, ErrorNotice } from '@/ui/primitives';
import {
  describeFiles,
  frameCountWarning,
  recountDisabled,
  recountLabel,
  recountNote,
  type RecountPhase,
} from './frameCount';

export function FrameCountNotice({
  taskId,
  media,
}: {
  taskId: string;
  media: TaskMediaMeta | undefined;
}) {
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<RecountPhase>('idle');
  const [error, setError] = useState<unknown>(null);

  const recount = useCallback(async () => {
    setError(null);
    setPhase('working');
    try {
      await api.recountFrames(taskId);
      setPhase('queued');
      // The job may finish before anyone looks again, so re-read the metadata rather than
      // leaving the page asserting a warning the server has since withdrawn. This is one
      // refetch, not a poll: a decode takes as long as it takes, and hammering the endpoint
      // to watch it would cost more than reopening the page.
      await queryClient.invalidateQueries({ queryKey: ['task-media', taskId] });
    } catch (caught) {
      setError(caught);
      setPhase('failed');
    }
  }, [queryClient, taskId]);

  const warning = frameCountWarning(media);
  if (!warning) return null;

  const files = describeFiles(warning);
  const note = recountNote(phase);

  return (
    <div
      className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-3 text-sm text-amber-200"
      data-frame-count-warning=""
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <div className="min-w-0">
            <p className="font-medium">{warning.headline}</p>
            <p className="mt-1 text-amber-200/80">{warning.detail}</p>
            {files && (
              <p className="mt-1 truncate text-xs text-amber-200/70" data-estimated-files="">
                {files}
              </p>
            )}
            {note && <p className="mt-1 text-xs text-amber-200/70">{note}</p>}
          </div>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => void recount()}
          disabled={recountDisabled(phase)}
          data-recount-frames=""
        >
          <RefreshCw size={13} />
          {recountLabel(phase)}
        </Button>
      </div>
      {error != null && (
        <div className="mt-2">
          <ErrorNotice error={error} />
        </div>
      )}
    </div>
  );
}
