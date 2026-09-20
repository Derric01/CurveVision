/**
 * Accept or reject what a model proposed, in bulk.
 *
 * Renders nothing at all when there is nothing waiting, so it costs a job with no model
 * suggestions no space in an already-full rail.
 *
 * The decisions live in `suggestions.ts` and `canvas/types.isUnreviewed`; this is the
 * wiring and the copy.
 */

import { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Check, X } from 'lucide-react';
import { api } from '@/api/client';
import type { AnnotationDocument } from '@/api/types';
import { Button, ErrorNotice } from '@/ui/primitives';
import { describeDecision, describePending, pendingReview } from './suggestions';

export function SuggestionsPanel({
  jobId,
  document,
  onDecided,
}: {
  jobId: string;
  document: AnnotationDocument | undefined;
  onDecided: () => void;
}) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState<'accept' | 'reject' | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const review = pendingReview(document);

  const decide = useCallback(
    async (accepted: boolean) => {
      setError(null);
      setOutcome(null);
      setBusy(accepted ? 'accept' : 'reject');
      try {
        const counts = await api.decideSuggestions(
          jobId,
          { shapeIds: review.shapeIds, trackIds: review.trackIds, tagIds: review.tagIds },
          accepted,
        );
        setOutcome(describeDecision(counts, accepted));
        // Accepting clears a confidence and rejecting deletes rows; either way the frame on
        // screen is now wrong until it is re-read.
        await queryClient.invalidateQueries({ queryKey: ['annotations', jobId] });
        onDecided();
      } catch (caught) {
        setError(caught);
      } finally {
        setBusy(null);
      }
    },
    [jobId, onDecided, queryClient, review.shapeIds, review.tagIds, review.trackIds],
  );

  // Nothing pending and nothing to report: the section does not exist.
  if (review.total === 0 && outcome === null && error == null) return null;

  return (
    <div className="border-t border-ink-800 px-3 py-2" data-suggestions="">
      <h3 className="py-1 text-xs font-medium uppercase tracking-wide text-ink-500">
        Suggestions
      </h3>

      {review.total > 0 && (
        <>
          <p className="text-[11px] text-ink-400" data-suggestions-pending="">
            {describePending(review.total)}
          </p>
          <p className="mt-1 text-[11px] text-ink-600">
            Accepting keeps them as they are, still recorded as a model's work. Rejecting
            deletes them. Editing one accepts it on its own.
          </p>
          <div className="mt-2 flex gap-1.5">
            <Button
              variant="secondary"
              size="sm"
              className="flex-1"
              onClick={() => void decide(true)}
              disabled={busy !== null}
              data-suggestions-accept=""
            >
              <Check size={12} />
              {busy === 'accept' ? 'Keeping…' : `Accept ${review.total}`}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              className="flex-1"
              onClick={() => void decide(false)}
              disabled={busy !== null}
              data-suggestions-reject=""
            >
              <X size={12} />
              {busy === 'reject' ? 'Deleting…' : 'Reject all'}
            </Button>
          </div>
        </>
      )}

      {outcome && (
        <p className="mt-1.5 text-[11px] text-ink-400" data-suggestions-outcome="">
          {outcome}
        </p>
      )}
      {error != null && (
        <div className="mt-2">
          <ErrorNotice error={error} />
        </div>
      )}
    </div>
  );
}
