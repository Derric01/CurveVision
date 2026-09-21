import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, Eye } from 'lucide-react';
import { api } from '@/api/client';
import type { JobListing } from '@/api/types';
import { Badge, EmptyState, Panel, Spinner, jobStateTone } from '@/ui/primitives';
import { describeJobLocation, partitionReviewQueue, summariseReviewQueue } from './myWork';

/**
 * Everything the signed-in person is on the hook for: what they are drawing, and what is
 * waiting on them to check.
 *
 * The second half is new. `GET /jobs?mine=true` filters on `assignee_id` alone, so a named
 * reviewer had no way to ask for their own queue — they opened each task and read its job
 * list. Two queries rather than one, because they are two different questions and the
 * server narrows on each independently.
 */
export function MyWorkPage() {
  const jobs = useQuery({
    queryKey: ['my-jobs'],
    queryFn: () => api.jobs({ mine: true, limit: 100 }),
  });
  const reviewing = useQuery({
    queryKey: ['my-reviews'],
    queryFn: () => api.jobs({ reviewing: true, limit: 100 }),
  });
  const queue = partitionReviewQueue(reviewing.data?.results ?? []);

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="mb-1 text-lg font-semibold tracking-tight">My work</h1>
      <p className="mb-6 text-sm text-ink-400">
        Jobs assigned to you, and jobs waiting on your review, across every project.
      </p>

      {/* The queue first: somebody else is blocked on it, which the annotating half is not. */}
      <Panel title="To review" className="mb-6">
        {reviewing.isLoading ? (
          <Spinner />
        ) : queue.waiting.length > 0 || queue.later.length > 0 ? (
          <>
            <p className="mb-3 text-sm text-ink-400" data-review-queue-summary>
              {summariseReviewQueue(queue)}
            </p>
            {queue.waiting.length > 0 && <JobList jobs={queue.waiting} hook="review-waiting" />}
            {queue.later.length > 0 && (
              <div className="mt-4 border-t border-ink-800 pt-3">
                <p className="mb-1 text-xs uppercase tracking-wide text-ink-500">
                  Named to you, not submitted yet
                </p>
                <JobList jobs={queue.later} hook="review-later" />
              </div>
            )}
          </>
        ) : (
          <EmptyState
            icon={<Eye size={32} />}
            title="No jobs name you as reviewer"
            description="A job assigned to you for review will appear here, and moves to the top of this list when the annotator submits it."
          />
        )}
      </Panel>

      <Panel title="Assigned to me">
        {jobs.isLoading ? (
          <Spinner />
        ) : jobs.data && jobs.data.results.length > 0 ? (
          <JobList jobs={jobs.data.results} hook="assigned" />
        ) : (
          <EmptyState
            icon={<CheckCircle2 size={32} />}
            title="Nothing assigned to you"
            description="Jobs assigned to you will appear here."
          />
        )}
      </Panel>
    </div>
  );
}

function JobList({ jobs, hook }: { jobs: JobListing[]; hook: string }) {
  return (
    <ul className="divide-y divide-ink-800" data-job-list={hook}>
      {jobs.map((job) => (
        <li key={job.id}>
          <Link
            to={`/jobs/${job.id}`}
            className="flex items-center justify-between py-3 hover:text-curve-300"
            data-job-row={job.id}
          >
            <div>
              <p className="text-sm text-ink-100">{describeJobLocation(job)}</p>
              <p className="mt-0.5 font-mono text-xs text-ink-500">
                frames {job.start_frame}–{job.stop_frame}
              </p>
            </div>
            <Badge tone={jobStateTone(job.state)}>{job.state.replace('_', ' ')}</Badge>
          </Link>
        </li>
      ))}
    </ul>
  );
}
