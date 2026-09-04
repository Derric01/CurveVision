import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle2 } from 'lucide-react';
import { api } from '@/api/client';
import { Badge, EmptyState, Panel, Spinner, jobStateTone } from '@/ui/primitives';

/** Everything assigned to the signed-in annotator, newest first. */
export function MyWorkPage() {
  const jobs = useQuery({
    queryKey: ['my-jobs'],
    queryFn: () => api.jobs({ mine: true, limit: 100 }),
  });

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="mb-1 text-lg font-semibold tracking-tight">My work</h1>
      <p className="mb-6 text-sm text-ink-400">Jobs assigned to you across every project.</p>

      <Panel>
        {jobs.isLoading ? (
          <Spinner />
        ) : jobs.data && jobs.data.results.length > 0 ? (
          <ul className="divide-y divide-ink-800">
            {jobs.data.results.map((job) => (
              <li key={job.id}>
                <Link
                  to={`/jobs/${job.id}`}
                  className="flex items-center justify-between py-3 hover:text-curve-300"
                >
                  <div>
                    <p className="text-sm text-ink-100">Job #{job.index + 1}</p>
                    <p className="mt-0.5 font-mono text-xs text-ink-500">
                      frames {job.start_frame}–{job.stop_frame}
                    </p>
                  </div>
                  <Badge tone={jobStateTone(job.state)}>{job.state.replace('_', ' ')}</Badge>
                </Link>
              </li>
            ))}
          </ul>
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
