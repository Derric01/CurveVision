/**
 * The annotation editor.
 *
 * Layout: tool rail on the left, canvas in the middle, labels and objects on the right,
 * frame navigation along the bottom. Chrome is kept minimal because canvas area is the
 * scarcest resource in the product.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleDashed,
  Eye,
  EyeOff,
  Hand,
  Lock,
  LockOpen,
  Maximize2,
  MousePointer2,
  Pentagon,
  Redo2,
  Send,
  Spline,
  Square,
  Trash2,
  Undo2,
  Wand2,
} from 'lucide-react';
import clsx from 'clsx';
import { api } from '@/api/client';
import type { AnnotationChange, LabelStyle, ToolName } from '@/canvas/types';
import { Badge, Button, ErrorNotice, Kbd, Spinner, jobStateTone } from '@/ui/primitives';
import { AnnotationCanvas, type CanvasHandle } from './AnnotationCanvas';
import { frameAnnotations, toLabelStyles } from './adapters';
import { useFrameObjectUrl } from './useFrameObjectUrl';
import { useAutosave } from './useAutosave';

const TOOLS: { name: ToolName; icon: typeof Square; label: string; key: string }[] = [
  { name: 'select', icon: MousePointer2, label: 'Select', key: 'V' },
  { name: 'pan', icon: Hand, label: 'Pan', key: 'H' },
  { name: 'rectangle', icon: Square, label: 'Rectangle', key: 'R' },
  { name: 'polygon', icon: Pentagon, label: 'Polygon', key: 'P' },
  { name: 'polyline', icon: Spline, label: 'Polyline', key: 'L' },
  { name: 'ellipse', icon: CircleDashed, label: 'Ellipse', key: 'E' },
];

export function EditorPage() {
  const { jobId = '' } = useParams();
  const queryClient = useQueryClient();
  const canvasRef = useRef<CanvasHandle>(null);

  const [frame, setFrame] = useState<number | null>(null);
  const [tool, setTool] = useState<ToolName>('select');
  const [activeLabelId, setActiveLabelId] = useState<string | null>(null);
  const [selection, setSelection] = useState<string[]>([]);
  const [labelStyles, setLabelStyles] = useState<LabelStyle[]>([]);
  const [zoom, setZoom] = useState(1);

  const job = useQuery({ queryKey: ['job', jobId], queryFn: () => api.job(jobId) });
  const task = useQuery({
    queryKey: ['task', job.data?.task_id],
    queryFn: () => api.task(job.data!.task_id),
    enabled: !!job.data?.task_id,
  });
  const labels = useQuery({
    queryKey: ['labels', task.data?.project_id],
    queryFn: () => api.labels(task.data!.project_id),
    enabled: !!task.data?.project_id,
  });
  const annotations = useQuery({
    queryKey: ['annotations', jobId],
    queryFn: () => api.annotations(jobId),
  });

  // Start on the job's first frame rather than frame 0: a job is a slice of a task.
  useEffect(() => {
    if (frame === null && job.data) setFrame(job.data.start_frame);
  }, [frame, job.data]);

  useEffect(() => {
    if (labels.data && labelStyles.length === 0) {
      const styles = toLabelStyles(labels.data);
      setLabelStyles(styles);
      setActiveLabelId(styles[0]?.id ?? null);
    }
  }, [labels.data, labelStyles.length]);

  const version = annotations.data?.annotation_version ?? 0;

  const onSaved = useCallback(
    () => void queryClient.invalidateQueries({ queryKey: ['annotations', jobId] }),
    [jobId, queryClient],
  );
  const autosave = useAutosave(jobId, version, onSaved);

  const currentFrame = frame ?? job.data?.start_frame ?? 0;
  const visible = useMemo(
    () => frameAnnotations(annotations.data, currentFrame),
    [annotations.data, currentFrame],
  );

  const imageUrl = useFrameObjectUrl(task.data?.id, currentFrame);

  const handleChange = useCallback(
    (change: AnnotationChange) => autosave.record(change),
    [autosave],
  );

  const submitJob = useMutation({
    mutationFn: async () => {
      await autosave.flush();
      return api.updateJob(jobId, { state: 'submitted' });
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['job', jobId] }),
  });

  // Frame navigation. Arrow keys are the highest-traffic shortcut in the whole editor.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      if (!job.data) return;

      if (event.key === 'ArrowRight' || event.key === 'd') {
        setFrame((current) => Math.min(job.data!.stop_frame, (current ?? 0) + 1));
      } else if (event.key === 'ArrowLeft' || event.key === 'a') {
        setFrame((current) => Math.max(job.data!.start_frame, (current ?? 0) - 1));
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [job.data]);

  // Flush pending work before leaving the frame, so changes are never stranded.
  useEffect(() => {
    return () => void autosave.flush();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentFrame]);

  if (job.isLoading || task.isLoading || annotations.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }
  if (job.error) return <ErrorNotice error={job.error} />;

  const engine = canvasRef.current?.engine ?? null;
  const frameCount = (job.data?.stop_frame ?? 0) - (job.data?.start_frame ?? 0) + 1;

  return (
    <div className="flex h-full flex-col bg-ink-950">
      {/* Header */}
      <header className="flex h-11 shrink-0 items-center justify-between border-b border-ink-800 px-3">
        <div className="flex items-center gap-3">
          <Link
            to={`/tasks/${job.data?.task_id}`}
            className="flex items-center gap-1.5 text-xs text-ink-400 hover:text-ink-200"
          >
            <ArrowLeft size={14} />
            {task.data?.name}
          </Link>
          <Badge tone={jobStateTone(job.data?.state ?? 'new')}>
            {job.data?.state.replace('_', ' ')}
          </Badge>
          {job.data?.locked && (
            <Badge tone="warning">
              <Lock size={10} className="mr-1" />
              locked
            </Badge>
          )}
        </div>

        <div className="flex items-center gap-3">
          <SaveIndicator status={autosave.status} pending={autosave.pendingCount} />
          <Button size="sm" onClick={() => void autosave.flush()} disabled={autosave.pendingCount === 0}>
            Save
          </Button>
          <Button
            size="sm"
            variant="primary"
            onClick={() => submitJob.mutate()}
            disabled={submitJob.isPending || job.data?.state === 'submitted'}
          >
            <Send size={13} />
            Submit
          </Button>
        </div>
      </header>

      {autosave.error && (
        <div className="border-b border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs text-red-300">
          {autosave.error}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* Tool rail */}
        <nav className="flex w-12 shrink-0 flex-col items-center gap-1 border-r border-ink-800 py-2">
          {TOOLS.map(({ name, icon: Icon, label, key }) => (
            <button
              key={name}
              type="button"
              title={`${label} (${key})`}
              onClick={() => setTool(name)}
              className={clsx(
                'flex h-9 w-9 items-center justify-center rounded-md transition-colors',
                tool === name
                  ? 'bg-curve-500 text-ink-950'
                  : 'text-ink-400 hover:bg-ink-800 hover:text-ink-100',
              )}
            >
              <Icon size={16} />
            </button>
          ))}

          <div className="my-1 h-px w-6 bg-ink-800" />

          <RailButton title="Undo (Ctrl+Z)" onClick={() => engine?.handleKey('z', { ctrl: true })}>
            <Undo2 size={16} />
          </RailButton>
          <RailButton
            title="Redo (Ctrl+Shift+Z)"
            onClick={() => engine?.handleKey('z', { ctrl: true, shift: true })}
          >
            <Redo2 size={16} />
          </RailButton>
          <RailButton title="Delete selection (Del)" onClick={() => engine?.deleteSelection()}>
            <Trash2 size={16} />
          </RailButton>
          <RailButton title="Fit to frame" onClick={() => engine?.fitView()}>
            <Maximize2 size={16} />
          </RailButton>
        </nav>

        {/* Canvas */}
        <div className="relative min-w-0 flex-1">
          <AnnotationCanvas
            ref={canvasRef}
            annotations={visible}
            labels={labelStyles}
            imageUrl={imageUrl}
            activeLabelId={activeLabelId}
            tool={tool}
            onChange={handleChange}
            onSelectionChange={setSelection}
            onViewportChange={setZoom}
          />

          <div className="pointer-events-none absolute bottom-2 left-2 flex gap-2 text-[11px] text-ink-500">
            <span className="rounded bg-ink-900/80 px-1.5 py-0.5">{Math.round(zoom * 100)}%</span>
            <span className="rounded bg-ink-900/80 px-1.5 py-0.5">
              {visible.length} on frame
            </span>
          </div>
        </div>

        {/* Right panel */}
        <aside className="flex w-72 shrink-0 flex-col border-l border-ink-800">
          <LabelPanel
            labels={labelStyles}
            activeLabelId={activeLabelId}
            onSelect={setActiveLabelId}
            onToggleVisible={(id, visible_) => {
              setLabelStyles((current) =>
                current.map((label) => (label.id === id ? { ...label, visible: visible_ } : label)),
              );
              engine?.scene.setLabelVisible(id, visible_);
            }}
            onToggleLocked={(id, locked) => {
              setLabelStyles((current) =>
                current.map((label) => (label.id === id ? { ...label, locked } : label)),
              );
              engine?.scene.setLabelLocked(id, locked);
            }}
          />

          <ObjectList
            annotations={visible}
            labels={labelStyles}
            selection={selection}
            onFocus={(id) => engine?.focusAnnotation(id)}
          />

          <ShortcutHelp />
        </aside>
      </div>

      {/* Frame navigation */}
      <footer className="flex h-12 shrink-0 items-center gap-3 border-t border-ink-800 px-3">
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setFrame(Math.max(job.data!.start_frame, currentFrame - 1))}
          disabled={currentFrame <= (job.data?.start_frame ?? 0)}
        >
          <ChevronLeft size={16} />
        </Button>

        <input
          type="range"
          min={job.data?.start_frame ?? 0}
          max={job.data?.stop_frame ?? 0}
          value={currentFrame}
          onChange={(event) => setFrame(Number(event.target.value))}
          className="h-1 flex-1 cursor-pointer appearance-none rounded-full bg-ink-800 accent-curve-500"
          aria-label="Frame"
        />

        <Button
          size="sm"
          variant="ghost"
          onClick={() => setFrame(Math.min(job.data!.stop_frame, currentFrame + 1))}
          disabled={currentFrame >= (job.data?.stop_frame ?? 0)}
        >
          <ChevronRight size={16} />
        </Button>

        <span className="w-32 text-right font-mono text-xs tabular-nums text-ink-400">
          {currentFrame - (job.data?.start_frame ?? 0) + 1} / {frameCount}
        </span>
      </footer>
    </div>
  );
}

function RailButton({
  title,
  onClick,
  children,
}: {
  title: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className="flex h-9 w-9 items-center justify-center rounded-md text-ink-400 hover:bg-ink-800 hover:text-ink-100"
    >
      {children}
    </button>
  );
}

function SaveIndicator({ status, pending }: { status: string; pending: number }) {
  if (status === 'saving') {
    return (
      <span className="flex items-center gap-1.5 text-xs text-ink-400">
        <Spinner className="h-3 w-3" />
        Saving
      </span>
    );
  }
  if (status === 'conflict') return <Badge tone="danger">Conflict</Badge>;
  if (status === 'error') return <Badge tone="danger">Save failed</Badge>;
  if (pending > 0) return <Badge tone="warning">{pending} unsaved</Badge>;
  if (status === 'saved') {
    return (
      <span className="flex items-center gap-1 text-xs text-emerald-400">
        <Check size={13} />
        Saved
      </span>
    );
  }
  return null;
}

function LabelPanel({
  labels,
  activeLabelId,
  onSelect,
  onToggleVisible,
  onToggleLocked,
}: {
  labels: LabelStyle[];
  activeLabelId: string | null;
  onSelect: (id: string) => void;
  onToggleVisible: (id: string, visible: boolean) => void;
  onToggleLocked: (id: string, locked: boolean) => void;
}) {
  return (
    <div className="border-b border-ink-800">
      <h3 className="px-3 py-2 text-xs font-medium uppercase tracking-wide text-ink-500">Labels</h3>
      {/* Proportional rather than a fixed 13rem: six labels overflowed that cap and the
          list was cut through the middle of a row. It still scrolls on a short window —
          the object list below has to keep its share — but a normal window shows the
          whole schema. */}
      <ul className="max-h-[30vh] overflow-auto pb-2">
        {labels.map((label, index) => (
          <li key={label.id}>
            <div
              className={clsx(
                'flex items-center gap-2 px-3 py-1.5 text-sm',
                activeLabelId === label.id && 'bg-ink-850',
              )}
            >
              <button
                type="button"
                className="flex min-w-0 flex-1 items-center gap-2 text-left"
                onClick={() => onSelect(label.id)}
              >
                <span
                  className="h-3 w-3 shrink-0 rounded-sm"
                  style={{ backgroundColor: label.color }}
                />
                <span className="truncate text-ink-200">{label.name}</span>
                {index < 9 && <Kbd>{index + 1}</Kbd>}
              </button>
              <button
                type="button"
                title={label.visible ? 'Hide' : 'Show'}
                onClick={() => onToggleVisible(label.id, !label.visible)}
                className="text-ink-500 hover:text-ink-200"
              >
                {label.visible ? <Eye size={13} /> : <EyeOff size={13} />}
              </button>
              <button
                type="button"
                title={label.locked ? 'Unlock' : 'Lock'}
                onClick={() => onToggleLocked(label.id, !label.locked)}
                className="text-ink-500 hover:text-ink-200"
              >
                {label.locked ? <Lock size={13} /> : <LockOpen size={13} />}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ObjectList({
  annotations,
  labels,
  selection,
  onFocus,
}: {
  annotations: { id: string; labelId: string; shapeType: string; source: string; confidence?: number | null }[];
  labels: LabelStyle[];
  selection: string[];
  onFocus: (id: string) => void;
}) {
  const colorOf = (labelId: string) => labels.find((l) => l.id === labelId)?.color ?? '#38bdf8';
  const nameOf = (labelId: string) => labels.find((l) => l.id === labelId)?.name ?? 'unlabelled';

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <h3 className="sticky top-0 bg-ink-950 px-3 py-2 text-xs font-medium uppercase tracking-wide text-ink-500">
        Objects ({annotations.length})
      </h3>
      {annotations.length === 0 ? (
        <p className="px-3 py-4 text-xs text-ink-600">Nothing annotated on this frame yet.</p>
      ) : (
        <ul className="pb-2">
          {annotations.map((annotation) => (
            <li key={annotation.id}>
              {/* Fully keyboard reachable: the canvas itself cannot be, so the object list
                  is how the editor stays navigable without a mouse. */}
              <button
                type="button"
                onClick={() => onFocus(annotation.id)}
                className={clsx(
                  'flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs',
                  selection.includes(annotation.id)
                    ? 'bg-curve-500/10 text-curve-200'
                    : 'text-ink-300 hover:bg-ink-850',
                )}
              >
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-sm"
                  style={{ backgroundColor: colorOf(annotation.labelId) }}
                />
                <span className="truncate">{nameOf(annotation.labelId)}</span>
                <span className="ml-auto text-ink-600">{annotation.shapeType}</span>
                {annotation.source === 'model' && (
                  <Wand2 size={11} className="text-amber-400" aria-label="Model suggestion" />
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ShortcutHelp() {
  return (
    <details className="border-t border-ink-800 px-3 py-2 text-xs text-ink-500">
      <summary className="cursor-pointer select-none">Keyboard shortcuts</summary>
      <dl className="mt-2 space-y-1">
        {[
          ['V / R / P / L / E', 'Select, rectangle, polygon, polyline, ellipse'],
          ['Space (hold)', 'Pan'],
          ['← / →', 'Previous / next frame'],
          ['Enter', 'Finish polygon'],
          ['Backspace', 'Remove last vertex'],
          ['Ctrl+Z / Ctrl+Shift+Z', 'Undo / redo'],
          ['Ctrl+A', 'Select all'],
          ['Ctrl+D', 'Duplicate'],
          ['Del', 'Delete selection'],
          ['Esc', 'Cancel / deselect'],
        ].map(([keys, description]) => (
          <div key={keys} className="flex justify-between gap-3">
            <dt className="font-mono text-[10px] text-ink-400">{keys}</dt>
            <dd className="text-right text-[10px]">{description}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
