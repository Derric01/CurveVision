import { useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Download, ImagePlus, Pencil, Plus, Tag, Trash2, X } from 'lucide-react';
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
  labelToPayload,
  nextLabelColor,
  normaliseLabelName,
} from './labelSchema';
import {
  ATTRIBUTE_TYPES,
  type AttributeDraft,
  attributeProblem,
  blankAttribute,
  defaultChoices,
  draftFromAttribute,
  draftToPayload,
  isSaved,
  needsOptions,
} from './attributeSchema';

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
 * A project's label schema, and the ways it changes after the project exists.
 *
 * Until this existed the schema was fixed at creation from the application: the endpoints,
 * the policy and `api.createLabel` were all there, and nothing called the last of them. A
 * project that needed a `van` class had to get one from the SDK, the CLI or curl.
 *
 * Editing a label — its name, its colour and its attributes — goes through
 * `labelToPayload`, which rebuilds the whole label from the one the server reported. `PUT`
 * is a replace: a form that posted only the fields it changed would reset the label's
 * position, lift its shape restriction and delete every attribute definition on it. What
 * an attribute edit may change without stranding values already recorded is the server's
 * rule; `attributeSchema.ts` says what it is and how the form follows it.
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
  const [editing, setEditing] = useState<string | null>(null);

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
  const editLabel = useMutation({
    mutationFn: ({ label, changes }: { label: LabelSchemaEntry; changes: EditedLabel }) =>
      api.updateLabel(projectId, label.id, labelToPayload(label, {
        name: normaliseLabelName(changes.name),
        color: changes.color,
        // Always through the drafts, even when no attribute was touched: that is what
        // drops a default an older server accepted and this one would refuse, so a label
        // carrying one can still be renamed.
        attributes: changes.attributes.map(draftToPayload),
      })),
    onSuccess: () => {
      setEditing(null);
      refresh();
    },
  });

  return (
    <Panel title="Label schema">
      {labels.length > 0 ? (
        <ul className="space-y-2">
          {labels.map((label) => {
            const usage = describeLabelUsage(label.name, distribution);
            if (editing === label.id) {
              return (
                <li key={label.id} data-label={label.id}>
                  <LabelEditor
                    label={label}
                    pending={editLabel.isPending}
                    onCancel={() => setEditing(null)}
                    onSave={(changes) => editLabel.mutate({ label, changes })}
                  />
                </li>
              );
            }
            return (
              <li key={label.id} className="flex items-center gap-2 text-sm" data-label={label.id}>
                <span
                  className="h-3 w-3 shrink-0 rounded-sm"
                  style={{ backgroundColor: label.color }}
                />
                <span className="text-ink-200">{label.name}</span>
                {label.attributes.length > 0 && (
                  <span className="min-w-0 truncate text-xs text-ink-500" data-label-attributes="">
                    {label.attributes.map((attribute) => attribute.name).join(', ')}
                  </span>
                )}
                <button
                  type="button"
                  className="ml-auto shrink-0 rounded p-1 text-ink-500 hover:bg-ink-800 hover:text-curve-300"
                  aria-label={`Edit ${label.name}`}
                  data-label-edit={label.id}
                  onClick={() => setEditing(label.id)}
                >
                  <Pencil size={14} />
                </button>
                <button
                  type="button"
                  className="shrink-0 rounded p-1 text-ink-500 hover:bg-ink-800 hover:text-red-300 disabled:opacity-50"
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
      {(addLabel.error || removeLabel.error || editLabel.error) && (
        <div className="mt-3" data-label-error="">
          <ErrorNotice error={addLabel.error ?? removeLabel.error ?? editLabel.error} />
        </div>
      )}
    </Panel>
  );
}

interface EditedLabel {
  name: string;
  color: string;
  attributes: AttributeDraft[];
}

/**
 * One label's row, while it is being edited: its name and colour, and its attributes.
 *
 * Everything else about the label travels through `labelToPayload` untouched. Escape
 * cancels, because an edit started by accident should not need a mouse to get out of.
 * Save stays disabled while `attributeProblem` has something to say, and says it, because
 * the server's answer to those is a 422 that reaches the screen only as "One or more
 * fields are invalid".
 */
function LabelEditor({
  label,
  pending,
  onCancel,
  onSave,
}: {
  label: LabelSchemaEntry;
  pending: boolean;
  onCancel: () => void;
  onSave: (changes: EditedLabel) => void;
}) {
  const [name, setName] = useState(label.name);
  const [color, setColor] = useState(label.color);
  const [attributes, setAttributes] = useState(() => label.attributes.map(draftFromAttribute));
  const problem = attributeProblem(attributes);

  const change = (index: number, next: Partial<AttributeDraft>) =>
    setAttributes((drafts) =>
      drafts.map((draft, at) => (at === index ? { ...draft, ...next } : draft)),
    );

  return (
    <form
      className="space-y-2 rounded-md border border-ink-800 p-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (canAddLabel(name) && problem === null) onSave({ name, color, attributes });
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') onCancel();
      }}
    >
      <div className="flex items-center gap-2">
        <input
          type="color"
          className="h-8 w-8 shrink-0 cursor-pointer rounded-md border border-ink-700 bg-ink-950"
          value={color}
          aria-label={`Colour for ${label.name}`}
          data-label-edit-color=""
          onChange={(event) => setColor(event.target.value)}
        />
        <input
          className="h-8 w-full min-w-0 rounded-md border border-ink-700 bg-ink-950 px-2 text-sm text-ink-100 focus:border-curve-400"
          value={name}
          // The row was replaced by this form on a deliberate click, so the caret belongs
          // here; landing it anywhere else would make the pencil a two-step control.
          autoFocus
          aria-label={`Name for ${label.name}`}
          data-label-edit-name=""
          onChange={(event) => setName(event.target.value)}
        />
        <button
          type="submit"
          className="shrink-0 rounded p-1 text-ink-400 hover:bg-ink-800 hover:text-curve-300 disabled:opacity-50"
          aria-label="Save"
          data-label-edit-save=""
          disabled={pending || !canAddLabel(name) || problem !== null}
        >
          <Check size={14} />
        </button>
        <button
          type="button"
          className="shrink-0 rounded p-1 text-ink-500 hover:bg-ink-800 hover:text-ink-200"
          aria-label="Cancel"
          data-label-edit-cancel=""
          onClick={onCancel}
        >
          <X size={14} />
        </button>
      </div>

      {attributes.map((draft, index) => (
        <AttributeEditor
          // A saved attribute is keyed by its id; a new one by where it sits. Removing an
          // earlier row shifts that, which costs a remount and nothing more: the row holds
          // no state of its own, only the draft it is handed.
          key={draft.id ?? `new-${index}`}
          draft={draft}
          onChange={(next) => change(index, next)}
          onRemove={() => setAttributes((drafts) => drafts.filter((_, at) => at !== index))}
        />
      ))}
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="flex items-center gap-1 rounded px-1.5 py-1 text-xs text-ink-400 hover:bg-ink-800 hover:text-curve-300"
          data-attribute-add=""
          onClick={() => setAttributes((drafts) => [...drafts, blankAttribute()])}
        >
          <Plus size={12} /> Add attribute
        </button>
        {problem && (
          <span className="text-xs text-amber-300" data-attribute-problem="">
            {problem}
          </span>
        )}
      </div>
      {attributes.some((draft) => draft.default_value) && (
        <p className="text-xs text-ink-500">
          A default is recorded on every annotation saved with this label, and an attribute
          something is recorded under cannot be renamed or removed.
        </p>
      )}
    </form>
  );
}

/**
 * One attribute inside the label editor.
 *
 * A saved attribute's type and its "changes per frame" flag are shown but fixed, and its
 * saved options are shown as chips that cannot be removed: the server refuses all three,
 * because each would leave values already recorded unable to be saved again. A new
 * attribute has no such history, so every control is live.
 */
function AttributeEditor({
  draft,
  onChange,
  onRemove,
}: {
  draft: AttributeDraft;
  onChange: (next: Partial<AttributeDraft>) => void;
  onRemove: () => void;
}) {
  const saved = isSaved(draft);
  const choices = defaultChoices(draft);
  const fixed = saved ? 'Fixed once saved. Remove the attribute and add a new one to change it.' : undefined;
  const control =
    'h-7 rounded border border-ink-700 bg-ink-950 px-1.5 text-xs text-ink-100 focus:border-curve-400 disabled:opacity-60';

  return (
    <div
      className="space-y-1.5 rounded border border-ink-800 bg-ink-950/40 p-2"
      data-attribute-row={draft.id ?? 'new'}
    >
      <div className="flex items-center gap-2">
        <input
          className={`${control} w-full min-w-0`}
          value={draft.name}
          placeholder="occluded"
          aria-label="Attribute name"
          data-attribute-name=""
          onChange={(event) => onChange({ name: event.target.value })}
        />
        <select
          className={control}
          value={draft.attribute_type}
          disabled={saved}
          title={fixed}
          aria-label="Attribute type"
          data-attribute-type=""
          // A default chosen for the old type means nothing for the new one.
          onChange={(event) =>
            onChange({
              attribute_type: event.target.value as AttributeDraft['attribute_type'],
              default_value: '',
            })
          }
        >
          {ATTRIBUTE_TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="shrink-0 rounded p-1 text-ink-500 hover:bg-ink-800 hover:text-red-300"
          aria-label={`Remove attribute ${draft.name || '(new)'}`}
          data-attribute-remove=""
          onClick={onRemove}
        >
          <Trash2 size={12} />
        </button>
      </div>

      {needsOptions(draft.attribute_type) && (
        <div className="flex flex-wrap items-center gap-1">
          {draft.savedOptions.map((option) => (
            <span
              key={option}
              className="rounded bg-ink-800 px-1.5 py-0.5 text-xs text-ink-300"
              title="Saved options can be added to, not removed."
              data-attribute-saved-option=""
            >
              {option}
            </span>
          ))}
          <input
            className={`${control} min-w-0 flex-1`}
            value={draft.addedOptions}
            placeholder={saved ? 'add: green, grey' : 'options: red, blue'}
            aria-label="Options, comma separated"
            data-attribute-options=""
            onChange={(event) => onChange({ addedOptions: event.target.value })}
          />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 text-xs text-ink-400">
        <label className="flex items-center gap-1">
          Default
          {choices ? (
            <select
              className={control}
              // A default outside the choices is one an older server accepted; it is
              // dropped on save, so it is shown as what will be sent.
              value={choices.includes(draft.default_value) ? draft.default_value : ''}
              data-attribute-default=""
              onChange={(event) => onChange({ default_value: event.target.value })}
            >
              <option value="">none</option>
              {choices.map((choice) => (
                <option key={choice} value={choice}>
                  {choice}
                </option>
              ))}
            </select>
          ) : (
            <input
              className={`${control} w-24`}
              value={draft.default_value}
              placeholder="none"
              data-attribute-default=""
              onChange={(event) => onChange({ default_value: event.target.value })}
            />
          )}
        </label>
        <label className="flex items-center gap-1" title={fixed}>
          <input
            type="checkbox"
            checked={draft.mutable}
            disabled={saved}
            data-attribute-mutable=""
            onChange={(event) => onChange({ mutable: event.target.checked })}
          />
          changes per frame
        </label>
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
