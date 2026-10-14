/**
 * Accept or reject what a model proposed: the selected suggestions, or all of them, and go
 * to the next one waiting.
 *
 * Renders nothing at all when there is nothing waiting, so it costs a job with no model
 * suggestions no space in an already-full rail.
 *
 * The decisions live in `suggestions.ts` and `canvas/types.isUnreviewed`; this is the
 * wiring and the copy.
 */

import { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Check, ChevronRight, X } from 'lucide-react';
import { api } from '@/api/client';
import type { AnnotationDocument } from '@/api/types';
import { Button, ErrorNotice } from '@/ui/primitives';
import {
  describeDecision,
  describePending,
  nextSuggestion,
  pendingReview,
  selectedForReview,
  type ReviewSet,
  type SuggestionPlace,
} from './suggestions';

export function SuggestionsPanel({
  jobId,
  document,
  onDecided,
  selection = [],
  currentFrame = 0,
  beforeDeciding,
  onGoTo,
}: {
  jobId: string;
  document: AnnotationDocument | undefined;
  onDecided: () => void;
  /** What is selected on the canvas: the suggestions among it can be decided on their own. */
  selection?: readonly string[];
  currentFrame?: number;
  /**
   * Save what the editor holds first. A suggestion edited and not yet saved would otherwise
   * be decided as the model left it — or deleted under an edit still on its way.
   */
  beforeDeciding?: () => Promise<unknown>;
  /** Show a suggestion: its frame, with it selected. */
  onGoTo?: (place: SuggestionPlace) => void;
}) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState<'accept' | 'reject' | 'accept-one' | 'reject-one' | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const review = pendingReview(document);
  const selected = selectedForReview(document, selection);
  const next = nextSuggestion(document, {
    frame: currentFrame,
    id: selected.shapeIds[0] ?? selected.trackIds[0] ?? null,
  });

  const decide = useCallback(
    async (accepted: boolean, which: ReviewSet, one: boolean) => {
      setError(null);
      setOutcome(null);
      setBusy(one ? (accepted ? 'accept-one' : 'reject-one') : accepted ? 'accept' : 'reject');
      try {
        await beforeDeciding?.();
        const counts = await api.decideSuggestions(
          jobId,
          { shapeIds: which.shapeIds, trackIds: which.trackIds, tagIds: which.tagIds },
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
    [beforeDeciding, jobId, onDecided, queryClient],
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
          {selected.total > 0 && (
            <div className="mt-2 rounded-md border border-ink-700 p-2" data-suggestion-selected="">
              <p className="text-[11px] text-ink-300">
                {selected.total === 1
                  ? 'The selected object is a suggestion.'
                  : `${selected.total} selected objects are suggestions.`}
              </p>
              <div className="mt-1.5 flex gap-1.5">
                <Button
                  variant="primary"
                  size="sm"
                  className="flex-1"
                  onClick={() => void decide(true, selected, true)}
                  disabled={busy !== null}
                  data-suggestion-accept=""
                >
                  <Check size={12} />
                  {busy === 'accept-one' ? 'Keeping…' : 'Accept'}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  className="flex-1"
                  onClick={() => void decide(false, selected, true)}
                  disabled={busy !== null}
                  data-suggestion-reject=""
                >
                  <X size={12} />
                  {busy === 'reject-one' ? 'Deleting…' : 'Reject'}
                </Button>
              </div>
            </div>
          )}
          {next && onGoTo && (
            <Button
              variant="ghost"
              size="sm"
              className="mt-1.5 w-full"
              onClick={() => onGoTo(next)}
              title="Select the next suggestion waiting in this job, on its frame"
              data-suggestion-next=""
            >
              Next suggestion
              <ChevronRight size={12} />
            </Button>
          )}
          <div className="mt-2 flex gap-1.5">
            <Button
              variant="secondary"
              size="sm"
              className="flex-1"
              onClick={() => void decide(true, review, false)}
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
              onClick={() => void decide(false, review, false)}
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
