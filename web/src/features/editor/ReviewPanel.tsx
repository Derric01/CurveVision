/**
 * The reviewer's decision on a submitted job: accept it, or send it back with a reason.
 *
 * Renders nothing at all unless the job is submitted or this session has just decided one,
 * so it costs an ordinary job being annotated no space in an already-full rail — the same
 * rule `SuggestionsPanel` follows.
 *
 * It sits at the bottom of the rail, after the issues and the quality report, because that
 * is the order the decision is actually made in: read what is there, check the score, then
 * rule on it.
 *
 * No permission check happens here. Whether this caller may review is the policy engine's
 * answer, not the browser's, so a reviewer-less account gets the server's own 403 rendered
 * in place — the same thing the quality and ground-truth panels do rather than keeping a
 * second, quietly diverging copy of the role table client-side.
 */

import { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Check, Undo2 } from 'lucide-react';
import { api } from '@/api/client';
import type { Job } from '@/api/types';
import { Button, ErrorNotice } from '@/ui/primitives';
import { canReject, describeReviewOutcome, isReviewable } from './review';

export function ReviewPanel({ job }: { job: Job | undefined }) {
  const queryClient = useQueryClient();
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState<'accept' | 'reject' | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const jobId = job?.id;

  const decide = useCallback(
    async (accepted: boolean) => {
      if (!jobId) return;
      setError(null);
      setOutcome(null);
      setBusy(accepted ? 'accept' : 'reject');
      try {
        const reviewed = await api.reviewJob(jobId, accepted, comment.trim() || undefined);
        setOutcome(describeReviewOutcome(reviewed.state));
        setComment('');
        // A rejection files an issue carrying the comment, and either decision moves the
        // job's state — the badge in the header and the issues list are both stale now.
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['job', jobId] }),
          queryClient.invalidateQueries({ queryKey: ['issues', jobId] }),
        ]);
      } catch (caught) {
        setError(caught);
      } finally {
        setBusy(null);
      }
    },
    [comment, jobId, queryClient],
  );

  const reviewable = isReviewable(job?.state);
  if (!reviewable && outcome === null && error == null) return null;

  return (
    <div className="border-t border-ink-800 px-3 py-2" data-review="">
      <h3 className="py-1 text-xs font-medium uppercase tracking-wide text-ink-500">Review</h3>

      {reviewable && (
        <>
          <p className="text-[11px] text-ink-400" data-review-prompt="">
            This job has been submitted and is waiting for a decision.
          </p>
          <textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            rows={3}
            placeholder="Why is it going back? Required to send it back, optional to accept."
            className="mt-2 w-full resize-none rounded-md border border-ink-700 bg-ink-950 px-2 py-1.5 text-[11px] text-ink-100 placeholder:text-ink-600 focus:border-curve-400 focus:outline-none"
            aria-label="Why this job is going back"
            data-review-comment=""
          />
          <p className="mt-1 text-[11px] text-ink-600">
            Sending it back files your comment as an issue on the job, so the annotator sees
            why rather than only that it came back.
          </p>
          <div className="mt-2 flex gap-1.5">
            <Button
              variant="secondary"
              size="sm"
              className="flex-1"
              onClick={() => void decide(true)}
              disabled={busy !== null}
              data-review-accept=""
            >
              <Check size={12} />
              {busy === 'accept' ? 'Accepting…' : 'Accept'}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              className="flex-1"
              onClick={() => void decide(false)}
              disabled={busy !== null || !canReject(comment)}
              title={canReject(comment) ? undefined : 'Say what needs changing first'}
              data-review-reject=""
            >
              <Undo2 size={12} />
              {busy === 'reject' ? 'Sending back…' : 'Send back'}
            </Button>
          </div>
        </>
      )}

      {outcome && (
        <p className="mt-1.5 text-[11px] text-ink-400" data-review-outcome="">
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
