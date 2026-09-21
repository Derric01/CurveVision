import { useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, ImagePlus, Plus, Tag, Trash2 } from 'lucide-react';
import { api } from '@/api/client';
import type { Label as LabelSchemaEntry } from '@/api/types';
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
import {
  canAddLabel,
  describeLabelUsage,
  nextLabelColor,
  normaliseLabelName,
} from './labelSchema';

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
          <LabelSchemaPanel
            projectId={projectId}
            labels={labels}
            distribution={statistics.data?.label_distribution ?? {}}
          />

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

/**
 * A project's label schema, and the two ways it changes after the project exists.
 *
 * Until this existed the schema was fixed at creation from the application: the endpoints,
 * the policy and `api.createLabel` were all there, and nothing called the last of them. A
 * project that needed a `van` class had to get one from the SDK, the CLI or curl.
 *
 * Editing a label in place is deliberately **not** offered yet. `PUT` replaces a label's
 * attributes wholesale, so a form that did not carry the attribute schema would silently
 * delete it — that needs an attribute editor, which is its own piece of work.
 */
function LabelSchemaPanel({
  projectId,
  labels,
  distribution,
}: {
  projectId: string;
  labels: LabelSchemaEntry[];
  distribution: Record<string, number>;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [color, setColor] = useState(() => nextLabelColor(labels));

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['project', projectId] });
    void queryClient.invalidateQueries({ queryKey: ['project-statistics', projectId] });
  };

  const addLabel = useMutation({
    mutationFn: () =>
      api.createLabel(projectId, { name: normaliseLabelName(name), color }),
    onSuccess: (created) => {
      setName('');
      // The next proposal has to account for the colour just taken, which the query this
      // invalidates will not have delivered yet.
      setColor(nextLabelColor([...labels, created]));
      refresh();
    },
  });
  const removeLabel = useMutation({
    mutationFn: (labelId: string) => api.deleteLabel(projectId, labelId),
    onSuccess: refresh,
  });

  return (
    <Panel title="Label schema">
      {labels.length > 0 ? (
        <ul className="space-y-2">
          {labels.map((label) => {
            const usage = describeLabelUsage(label.name, distribution);
            return (
              <li key={label.id} className="flex items-center gap-2 text-sm" data-label={label.id}>
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
                <button
                  type="button"
                  className="ml-auto shrink-0 rounded p-1 text-ink-500 hover:bg-ink-800 hover:text-red-300 disabled:opacity-50"
                  title={usage ? `Remove ${label.name} — ${usage}` : `Remove ${label.name}`}
                  aria-label={`Remove ${label.name}`}
                  data-label-delete={label.id}
                  disabled={removeLabel.isPending}
                  onClick={() => removeLabel.mutate(label.id)}
                >
                  <Trash2 size={14} />
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <EmptyState icon={<Tag size={28} />} title="No labels defined" />
      )}

      {/* No permission check here: whether this caller may change the schema is the policy
          engine's answer, so the controls show and a 403 arrives as the server's own
          message — the same choice every other panel in this application makes. */}
      <form
        className="mt-4 flex items-end gap-2 border-t border-ink-800 pt-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (canAddLabel(name)) addLabel.mutate();
        }}
      >
        {/* `Input` puts its own className on the input element and wraps it in a
            block-level label, so the wrapper is what has to grow here. */}
        <div className="flex-1">
          <Input
            label="Add a label"
            placeholder="van"
            value={name}
            data-new-label=""
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <input
          type="color"
          className="h-9 w-9 shrink-0 cursor-pointer rounded-md border border-ink-700 bg-ink-950"
          value={color}
          aria-label="Label colour"
          data-new-label-color=""
          onChange={(event) => setColor(event.target.value)}
        />
        <Button type="submit" disabled={!canAddLabel(name) || addLabel.isPending} data-new-label-add="">
          <Plus size={14} /> Add
        </Button>
      </form>
      {(addLabel.error || removeLabel.error) && (
        <div className="mt-3" data-label-error="">
          <ErrorNotice error={addLabel.error ?? removeLabel.error} />
        </div>
      )}
    </Panel>
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
