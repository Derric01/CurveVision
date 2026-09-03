import { useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, ImagePlus, Plus, Tag } from 'lucide-react';
import { api } from '@/api/client';
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Input,
  Panel,
  ProgressBar,
  Spinner,
} from '@/ui/primitives';

export function ProjectPage() {
  const { projectId = '' } = useParams();
  const queryClient = useQueryClient();
  const [taskName, setTaskName] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);

  const project = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.project(projectId),
  });
  const tasks = useQuery({
    queryKey: ['tasks', projectId],
    queryFn: () => api.tasks({ project_id: projectId }),
  });
  const statistics = useQuery({
    queryKey: ['project-statistics', projectId],
    queryFn: () => api.projectStatistics(projectId),
  });
  const formats = useQuery({ queryKey: ['formats'], queryFn: () => api.formats() });

  const createTask = useMutation({
    mutationFn: async () => {
      const task = await api.createTask({ project_id: projectId, name: taskName });
      if (pendingFiles.length > 0) await api.uploadAssets(task.id, pendingFiles);
      return task;
    },
    onSuccess: () => {
      setTaskName('');
      setPendingFiles([]);
      if (fileInput.current) fileInput.current.value = '';
      void queryClient.invalidateQueries({ queryKey: ['tasks', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['project-statistics', projectId] });
    },
  });

  const exportDataset = useMutation({
    mutationFn: async (format: string) => {
      const blob = await api.exportProject(projectId, { format });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `${project.data?.slug ?? 'dataset'}-${format}.zip`;
      anchor.click();
      URL.revokeObjectURL(url);
    },
  });

  if (project.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }
  if (project.error) return <ErrorNotice error={project.error} />;

  const labels = project.data?.labels ?? [];

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-6 py-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">{project.data?.name}</h1>
          <p className="mt-0.5 text-sm text-ink-400">
            {project.data?.description || 'No description'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="h-9 rounded-md border border-ink-700 bg-ink-950 px-3 text-sm"
            defaultValue="coco"
            id="export-format"
          >
            {(formats.data ?? [])
              .filter((format) => format.supports_export)
              .map((format) => (
                <option key={format.id} value={format.id}>
                  {format.name}
                </option>
              ))}
          </select>
          <Button
            onClick={() => {
              const select = document.getElementById('export-format') as HTMLSelectElement | null;
              exportDataset.mutate(select?.value ?? 'coco');
            }}
            disabled={exportDataset.isPending}
          >
            <Download size={15} />
            Export
          </Button>
        </div>
      </header>

      {exportDataset.error && <ErrorNotice error={exportDataset.error} />}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Panel
            title="Tasks"
            actions={<span className="text-xs text-ink-500">{tasks.data?.count ?? 0} total</span>}
          >
            {tasks.isLoading ? (
              <Spinner />
            ) : tasks.data && tasks.data.results.length > 0 ? (
              <ul className="divide-y divide-ink-800">
                {tasks.data.results.map((task) => (
                  <li key={task.id}>
                    <Link
                      to={`/tasks/${task.id}`}
                      className="flex items-center justify-between gap-4 py-3 hover:text-curve-300"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm text-ink-100">{task.name}</p>
                        <p className="mt-0.5 text-xs text-ink-500">
                          {task.frame_count} frame{task.frame_count === 1 ? '' : 's'} ·{' '}
                          {task.media_kind}
                        </p>
                      </div>
                      <Badge tone={task.status === 'completed' ? 'success' : 'neutral'}>
                        {task.status.replace('_', ' ')}
                      </Badge>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState
                icon={<ImagePlus size={32} />}
                title="No tasks yet"
                description="A task holds the media for one batch of work."
              />
            )}
          </Panel>

          <Panel title="New task">
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault();
                createTask.mutate();
              }}
            >
              <Input
                label="Task name"
                value={taskName}
                onChange={(event) => setTaskName(event.target.value)}
                placeholder="Batch 1"
                required
              />
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-ink-300">Images</span>
                <input
                  ref={fileInput}
                  type="file"
                  multiple
                  accept="image/*,video/*"
                  onChange={(event) => setPendingFiles([...(event.target.files ?? [])])}
                  className="block w-full text-sm text-ink-400 file:mr-3 file:rounded-md file:border-0 file:bg-ink-800 file:px-3 file:py-1.5 file:text-sm file:text-ink-100 hover:file:bg-ink-700"
                />
                <span className="mt-1 block text-xs text-ink-500">
                  {pendingFiles.length > 0
                    ? `${pendingFiles.length} file(s) selected`
                    : 'Jobs are generated once media is uploaded.'}
                </span>
              </label>

              {createTask.error && <ErrorNotice error={createTask.error} />}

              <Button
                type="submit"
                variant="primary"
                disabled={createTask.isPending || !taskName.trim()}
              >
                <Plus size={15} />
                {createTask.isPending ? 'Creating…' : 'Create task'}
              </Button>
            </form>
          </Panel>
        </div>

        <div className="space-y-6">
          <Panel title="Label schema">
            {labels.length > 0 ? (
              <ul className="space-y-2">
                {labels.map((label) => (
                  <li key={label.id} className="flex items-center gap-2 text-sm">
                    <span
                      className="h-3 w-3 shrink-0 rounded-sm"
                      style={{ backgroundColor: label.color }}
                    />
                    <span className="text-ink-200">{label.name}</span>
                    {label.attributes.length > 0 && (
                      <span className="text-xs text-ink-500">
                        {label.attributes.length} attribute
                        {label.attributes.length === 1 ? '' : 's'}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState icon={<Tag size={28} />} title="No labels defined" />
            )}
          </Panel>

          <Panel title="Statistics">
            {statistics.data ? (
              <dl className="space-y-3 text-sm">
                <Stat label="Frames" value={statistics.data.frame_count} />
                <Stat label="Shapes" value={statistics.data.shape_count} />
                <Stat label="Tracks" value={statistics.data.track_count} />
                <Stat
                  label="Unannotated frames"
                  value={statistics.data.frames_without_annotations}
                  tone={statistics.data.frames_without_annotations > 0 ? 'warning' : 'neutral'}
                />

                {Object.keys(statistics.data.label_distribution).length > 0 && (
                  <div className="pt-2">
                    <p className="mb-2 text-xs font-medium text-ink-300">Class distribution</p>
                    <ClassDistribution distribution={statistics.data.label_distribution} />
                  </div>
                )}
              </dl>
            ) : (
              <Spinner />
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tone = 'neutral',
}: {
  label: string;
  value: number;
  tone?: 'neutral' | 'warning';
}) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-ink-400">{label}</dt>
      <dd className={tone === 'warning' && value > 0 ? 'text-amber-300' : 'text-ink-100'}>
        {value.toLocaleString()}
      </dd>
    </div>
  );
}

/**
 * Class distribution as proportional bars.
 *
 * The single most useful thing to look at before training: a class with a handful of
 * examples next to one with thousands explains a bad model faster than any metric.
 */
function ClassDistribution({ distribution }: { distribution: Record<string, number> }) {
  const entries = Object.entries(distribution).sort((a, b) => b[1] - a[1]);
  const max = Math.max(...entries.map(([, count]) => count), 1);

  return (
    <ul className="space-y-1.5">
      {entries.map(([name, count]) => (
        <li key={name}>
          <div className="mb-0.5 flex justify-between text-xs">
            <span className="text-ink-300">{name}</span>
            <span className="text-ink-500">{count.toLocaleString()}</span>
          </div>
          <ProgressBar value={count / max} />
        </li>
      ))}
    </ul>
  );
}
