import { useCallback, useEffect, useState } from 'react';
import clsx from 'clsx';
import { Link, useParams } from 'react-router-dom';
import { useQueryClient, useQuery } from '@tanstack/react-query';
import { ArrowLeft, FolderOpen, PenLine } from 'lucide-react';
import { api } from '@/api/client';
import { chooseFolder, isDesktop, onOpenFolder } from '@/desktop';
import { summariseImport, type ImportSummary } from './localImport';
import { GroundTruthPanel } from './GroundTruthPanel';
import { latestReportByJob, splitJobs } from './groundTruth';
import {
  Badge,
  Button,
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
  // Scores are a nice-to-have on this page, not a reason to fail it: a task with no
  // ground-truth job has no reports, and a viewer without the permission gets a 403. Either
  // way the job list must still render.
  const quality = useQuery({
    queryKey: ['task-quality', taskId],
    queryFn: () => api.taskQuality(taskId),
    retry: false,
  });

  const queryClient = useQueryClient();
  const [importing, setImporting] = useState(false);
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [importError, setImportError] = useState<unknown>(null);

  const importFolder = useCallback(async () => {
    if (!taskId) return;
    setImportError(null);
    let path: string | null;
    try {
      path = await chooseFolder('Choose a folder of images to annotate');
    } catch (error) {
      setImportError(error);
      return;
    }
    if (!path) return; // Cancelled, or no shell. Neither is an error.

    setImporting(true);
    setSummary(null);
    try {
      const result = await api.localImport(taskId, { path, recursive: true });
      setSummary(summariseImport(result));
      // The frame count, the job list and the project's statistics all move when media
      // is attached, so none of them may be left showing the state from before.
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['task', taskId] }),
        queryClient.invalidateQueries({ queryKey: ['jobs', taskId] }),
        queryClient.invalidateQueries({ queryKey: ['project'] }),
      ]);
    } catch (error) {
      setImportError(error);
    } finally {
      setImporting(false);
    }
  }, [queryClient, taskId]);

  // File ▸ Open Folder… (Cmd/Ctrl+O) does exactly what the button does. In a browser this
  // subscribes to nothing and unsubscribes from nothing.
  useEffect(() => onOpenFolder(() => void importFolder()), [importFolder]);

  if (task.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }
  if (task.error) return <ErrorNotice error={task.error} />;

  const progress = task.data?.progress;
  const split = splitJobs(jobs.data);
  const scores = latestReportByJob(quality.data);

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

      {isDesktop() && (
        <Panel
          title="Add media from this computer"
          actions={
            <Button variant="secondary" size="sm" onClick={() => void importFolder()} disabled={importing}>
              <FolderOpen size={13} />
              {importing ? 'Importing…' : 'Choose folder…'}
            </Button>
          }
        >
          <p className="text-sm text-ink-400">
            Images are annotated where they are. Nothing is copied into the application, so a
            folder of any size is attached in the time it takes to list it.
          </p>
          {importError !== null && (
            <div className="mt-3">
              <ErrorNotice error={importError} />
            </div>
          )}
          {summary && (
            <div
              className={clsx(
                'mt-3 rounded-md border px-3 py-2 text-sm',
                summary.tone === 'success'
                  ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200'
                  : 'border-amber-500/30 bg-amber-500/10 text-amber-200',
              )}
            >
              <p>{summary.headline}</p>
              {summary.skipped.length > 0 && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs opacity-80 hover:opacity-100">
                    Show the {summary.skipped.length.toLocaleString()} file
                    {summary.skipped.length === 1 ? '' : 's'} that could not be read
                  </summary>
                  <ul className="mt-2 max-h-40 space-y-1 overflow-auto font-mono text-xs opacity-80">
                    {summary.skipped.map((reason, index) => (
                      <li key={`${index}-${reason}`}>{reason}</li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}
        </Panel>
      )}

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

      {task.data && (
        <GroundTruthPanel
          taskId={taskId}
          frameCount={task.data.frame_count}
          groundTruth={split.groundTruth}
        />
      )}

      <Panel title="Jobs">
        {jobs.isLoading ? (
          <Spinner />
        ) : split.annotation.length > 0 ? (
          <ul className="divide-y divide-ink-800">
            {split.annotation.map((job) => (
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
                    {(() => {
                      // The score, where there is one. Shown next to the state because the
                      // two answer different questions — "has it been reviewed" and "was it
                      // right" — and a job can be accepted with a poor score.
                      const report = scores.get(job.id);
                      if (!report) return null;
                      return (
                        <span
                          className="font-mono text-xs tabular-nums text-ink-400"
                          title={`Scored against ground truth at IoU ≥ ${report.iou_threshold}`}
                        >
                          F1 {Math.round(report.f1 * 100)}%
                          {report.annotation_version !== null &&
                            report.annotation_version !== job.annotation_version && (
                              <span className="ml-1 text-amber-400" title="The job has changed since it was scored">
                                stale
                              </span>
                            )}
                        </span>
                      );
                    })()}
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
            description={
              isDesktop()
                ? 'Jobs appear once this task has media. Choose a folder above, or upload images from the project page.'
                : 'Jobs appear once this task has media. Upload images from the project page.'
            }
          />
        )}
      </Panel>
    </div>
  );
}
