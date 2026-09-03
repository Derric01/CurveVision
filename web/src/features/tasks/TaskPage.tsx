import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, PenLine } from 'lucide-react';
import { api } from '@/api/client';
import {
  Badge,
  EmptyState,
  ErrorNotice,
  Panel,
  ProgressBar,
  Spinner,
  jobStateTone,
} from '@/ui/primitives';

export function TaskPage() {
  const { taskId = '' } = useParams();

  const task = useQuery({ queryKey: ['task', taskId], queryFn: () => api.task(taskId) });
  const jobs = useQuery({ queryKey: ['jobs', taskId], queryFn: () => api.taskJobs(taskId) });

  if (task.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }
  if (task.error) return <ErrorNotice error={task.error} />;

  const progress = task.data?.progress;

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-6 py-8">
      <header>
        <Link
          to={`/projects/${task.data?.project_id}`}
          className="mb-3 inline-flex items-center gap-1.5 text-xs text-ink-400 hover:text-ink-200"
        >
          <ArrowLeft size={13} />
          Back to project
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-lg font-semibold tracking-tight">{task.data?.name}</h1>
          <Badge tone={task.data?.status === 'completed' ? 'success' : 'neutral'}>
            {task.data?.status.replace('_', ' ')}
          </Badge>
        </div>
        <p className="mt-1 text-sm text-ink-400">
          {task.data?.frame_count.toLocaleString()} frames · {task.data?.media_kind}
        </p>
      </header>

      {progress && (
        <Panel title="Progress">
          <div className="mb-2 flex items-baseline justify-between">
            <span className="text-2xl font-semibold tabular-nums">
              {Math.round(progress.completion * 100)}%
            </span>
            <span className="text-xs text-ink-500">
              {progress.completed_frames.toLocaleString()} of{' '}
              {progress.total_frames.toLocaleString()} frames accepted
            </span>
          </div>
          <ProgressBar value={progress.completion} />
          <div className="mt-4 flex flex-wrap gap-2">
            {Object.entries(progress.jobs_by_state)
              .filter(([, count]) => count > 0)
              .map(([state, count]) => (
                <Badge key={state} tone={jobStateTone(state)}>
                  {state.replace('_', ' ')}: {count}
                </Badge>
              ))}
          </div>
        </Panel>
      )}

      <Panel title="Jobs">
        {jobs.isLoading ? (
          <Spinner />
        ) : jobs.data && jobs.data.length > 0 ? (
          <ul className="divide-y divide-ink-800">
            {jobs.data.map((job) => (
              <li key={job.id}>
                <Link
                  to={`/jobs/${job.id}`}
                  className="flex items-center justify-between gap-4 py-3 hover:text-curve-300"
                >
                  <div>
                    <p className="text-sm text-ink-100">
                      Job #{job.index + 1}
                      <span className="ml-2 font-mono text-xs text-ink-500">
                        frames {job.start_frame}–{job.stop_frame}
                      </span>
                    </p>
                    <p className="mt-0.5 text-xs text-ink-500">
                      {job.shape_count} shape{job.shape_count === 1 ? '' : 's'} ·{' '}
                      {job.track_count} track{job.track_count === 1 ? '' : 's'}
                      {job.assignee && ` · ${job.assignee.username}`}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <Badge tone={jobStateTone(job.state)}>{job.state.replace('_', ' ')}</Badge>
                    <PenLine size={15} className="text-ink-500" />
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            title="No jobs yet"
            description="Jobs appear once this task has media. Upload images from the project page."
          />
        )}
      </Panel>
    </div>
  );
}
