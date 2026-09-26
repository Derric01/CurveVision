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
  Box,
  Brush,
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
  PersonStanding,
  Redo2,
  Send,
  Spline,
  Square,
  Trash2,
  Scissors,
  Undo2,
  Wand2,
} from 'lucide-react';
import clsx from 'clsx';
import { api } from '@/api/client';
import { isUnreviewed } from '@/canvas/types';
import type { AnnotationChange, LabelStyle, ToolName } from '@/canvas/types';
import type { ApiTrack } from '@/api/types';
import { Badge, Button, ErrorNotice, Kbd, Spinner, jobStateTone } from '@/ui/primitives';
import { AnnotationCanvas, type CanvasHandle } from './AnnotationCanvas';
import { drawableLabels, frameAnnotations, toApiTrackUpdate, toLabelStyles } from './adapters';
import { useFrameObjectUrl } from './useFrameObjectUrl';
import { TrackTimeline } from './TrackTimeline';
import { adjacentKeyframe, trackRows } from './timeline';
import { markDeparture, moveKeyframe, toggleKeyframe, type EditResult } from './keyframes';
import { QualityPanel } from './QualityPanel';
import { IssuesPanel } from './IssuesPanel';
import { issuePins } from './issues';
import { skeletonHint } from './skeletonHint';
import { brushHint } from './brushHint';
import { cuboidHint } from './cuboidHint';
import { AutoAnnotatePanel } from './AutoAnnotatePanel';
import { SuggestionsPanel } from './SuggestionsPanel';
import { ReviewPanel } from './ReviewPanel';
import { canSubmit } from './review';
import { useAutosave } from './useAutosave';

const TOOLS: { name: ToolName; icon: typeof Square; label: string; key: string }[] = [
  { name: 'select', icon: MousePointer2, label: 'Select', key: 'V' },
  { name: 'pan', icon: Hand, label: 'Pan', key: 'H' },
  { name: 'rectangle', icon: Square, label: 'Rectangle', key: 'R' },
  { name: 'polygon', icon: Pentagon, label: 'Polygon', key: 'P' },
  { name: 'polyline', icon: Spline, label: 'Polyline', key: 'L' },
  { name: 'ellipse', icon: CircleDashed, label: 'Ellipse', key: 'E' },
  { name: 'cuboid', icon: Box, label: 'Cuboid (front face, then depth)', key: 'C' },
  { name: 'scissors', icon: Scissors, label: 'Scissors (snaps to edges)', key: 'S' },
  { name: 'skeleton', icon: PersonStanding, label: 'Skeleton (joints, in order)', key: 'J' },
  { name: 'brush', icon: Brush, label: 'Brush (paint a mask)', key: 'B' },
];

export function EditorPage() {
  const { jobId = '' } = useParams();
  const queryClient = useQueryClient();
  const canvasRef = useRef<CanvasHandle>(null);

  const [frame, setFrame] = useState<number | null>(null);
  const [tool, setTool] = useState<ToolName>('select');
  const [activeLabelId, setActiveLabelId] = useState<string | null>(null);
  // View settings, not annotation data: local to this session, reset on reload rather than
  // saved anywhere. `Scene` has always had these three; nothing before this iteration ever
  // called their setters, so they could not actually be turned off from the UI.
  const [showLabels, setShowLabels] = useState(true);
  const [showSuggestions, setShowSuggestions] = useState(true);
  const [fillOpacity, setFillOpacity] = useState(0.18);
  const [selection, setSelection] = useState<string[]>([]);
  const [labelStyles, setLabelStyles] = useState<LabelStyle[]>([]);
  // What a multi-step tool is waiting for. The skeleton tool is the only one that sets
  // it today: its joint order is invisible on the canvas, so clicking through a
  // seventeen-joint pose without it is clicking blind.
  const [toolStatus, setToolStatus] = useState<string | null>(null);
  // A status line belongs to whoever set it; switching away without clearing it would show,
  // say, skeleton joint text under the brush. Shared by the toolbar's own click and by
  // `onToolChange` below -- the same event a keyboard shortcut produces -- so both paths to
  // "the active tool changed" clear it the same way rather than one of them forgetting to.
  const activateTool = useCallback((next: ToolName) => {
    setTool(next);
    setToolStatus(null);
  }, []);
  const [zoom, setZoom] = useState(1);
  // Placing an issue pin: armed from the panel, spent by one canvas click.
  const [picking, setPicking] = useState(false);
  const [pickedPoint, setPickedPoint] = useState<{ x: number; y: number } | null>(null);
  const [openIssueId, setOpenIssueId] = useState<string | null>(null);


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
      // A joint is never the opening choice: it is not something anybody draws with.
      setActiveLabelId(drawableLabels(styles)[0]?.id ?? null);
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

  // The same query key the issues panel uses, so react-query serves both from one fetch
  // rather than this becoming a second request or a prop drilled through the sidebar.
  const issues = useQuery({
    queryKey: ['issues', jobId],
    queryFn: () => api.issues(jobId),
    enabled: Boolean(jobId),
    retry: false,
  });

  const pins = useMemo(() => {
    const placed = issuePins(issues.data, currentFrame, openIssueId);
    // The pin being placed right now is drawn too, before the issue exists. Without it the
    // reviewer clicks the image and nothing visibly happens, so they cannot tell whether
    // they hit the thing they meant until after the issue is filed.
    if (!pickedPoint) return placed;
    return [...placed, { id: 'draft', x: pickedPoint.x, y: pickedPoint.y, resolved: false, active: true }];
  }, [issues.data, currentFrame, openIssueId, pickedPoint]);

  const handleChange = useCallback(
    (change: AnnotationChange) => {
      // A tracked object is drawn under its track's id, so a deletion names the track. It
      // has to be sent as one: as a shape id it matches no shape and deletes nothing, and
      // the object is back the next time the job is opened.
      const trackIds = new Set(annotations.data?.tracks.map((track) => track.id));
      autosave.record({
        created: change.created,
        updated: change.updated,
        deletedIds: change.deletedIds.filter((id) => !trackIds.has(id)),
        deletedTrackIds: change.deletedIds.filter((id) => trackIds.has(id)),
      });
    },
    [autosave, annotations.data],
  );

  const submitJob = useMutation({
    mutationFn: async () => {
      await autosave.flush();
      return api.updateJob(jobId, { state: 'submitted' });
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['job', jobId] }),
  });

  const tracks = useMemo(
    () => trackRows(annotations.data, job.data?.start_frame ?? 0, job.data?.stop_frame ?? 0),
    [annotations.data, job.data?.start_frame, job.data?.stop_frame],
  );

  // The track whose keyframes `k` and `o` act on. A keyframe edit has to name one object,
  // and a job can hold dozens; without a selection the shortcuts would have to guess.
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(null);
  const [trackNotice, setTrackNotice] = useState<string | null>(null);

  const editTrack = useMutation({
    // Takes the track explicitly rather than reading `selectedTrackId`: a drag names the
    // lane it started on, and selection follows the drag rather than gating it.
    mutationFn: async ({
      trackId,
      edit,
    }: {
      trackId: string | null;
      edit: (track: ApiTrack) => EditResult;
    }) => {
      const track = annotations.data?.tracks.find((t) => t.id === trackId);
      if (!track) return { refused: 'Select a track on the timeline first.' };

      const result = edit(track);
      if (!result.ok) return { refused: result.refusal.reason };

      // Flush pending shape work first: both writes carry `annotation_version`, and sending
      // this one while the buffer still holds shapes would make one of them a 409.
      await autosave.flush();
      const current = await api.annotations(jobId);

      if (result.track === null) {
        await api.writeAnnotations(jobId, {
          annotation_version: current.annotation_version,
          deleted_tracks: [track.id],
        });
        return { refused: null };
      }
      await api.writeAnnotations(jobId, {
        annotation_version: current.annotation_version,
        updated_tracks: [toApiTrackUpdate(result.track)],
      });
      return { refused: null };
    },
    onSuccess: (result) => {
      setTrackNotice(result?.refused ?? null);
      if (!result?.refused) {
        void queryClient.invalidateQueries({ queryKey: ['annotations', jobId] });
      }
    },
    onError: (error: unknown) =>
      setTrackNotice(error instanceof Error ? error.message : 'The edit could not be saved.'),
  });

  // Every keyframe in the job, deduplicated — what `,` and `.` step between. Stepping
  // between keyframes rather than frames is how you move through a track that was
  // annotated every thirtieth frame without pressing an arrow thirty times.
  const keyframes = useMemo(() => {
    const all = new Set<number>();
    for (const track of tracks) for (const frame of track.keyframes) all.add(frame);
    return [...all].sort((a, b) => a - b);
  }, [tracks]);

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
      } else if (event.key === '.' || event.key === ',') {
        const next = adjacentKeyframe(
          keyframes,
          frame ?? job.data.start_frame,
          event.key === '.' ? 1 : -1,
        );
        if (next !== null) setFrame(next);
      } else if (event.key === 'k' && selectedTrackId) {
        event.preventDefault();
        editTrack.mutate({
          trackId: selectedTrackId,
          edit: (track) => toggleKeyframe(track, frame ?? job.data!.start_frame),
        });
      } else if (event.key === 'o' && selectedTrackId) {
        event.preventDefault();
        editTrack.mutate({
          trackId: selectedTrackId,
          edit: (track) => markDeparture(track, frame ?? job.data!.start_frame),
        });
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [job.data, keyframes, frame, selectedTrackId, editTrack]);

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
            disabled={submitJob.isPending || !canSubmit(job.data?.state)}
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

      {/* A submit can still fail for a reason the disabled state cannot predict -- somebody
          else moved the job, or this account cannot submit it at all. It used to fail
          silently: the mutation rejected and nothing rendered. */}
      {submitJob.error != null && (
        <div className="border-b border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs text-red-300">
          {submitJob.error instanceof Error ? submitJob.error.message : 'Could not submit this job.'}
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
              onClick={() => activateTool(name)}
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
            currentFrame={currentFrame}
            onChange={handleChange}
            onSelectionChange={setSelection}
            onViewportChange={setZoom}
            pins={pins}
            picking={picking}
            onPointPicked={(point) => {
              setPickedPoint(point);
              setPicking(false);
            }}
            onToolStatus={setToolStatus}
            // The engine is the source of truth for which tool is active: a keyboard
            // shortcut calls `engine.handleKey` directly, with no other path back to this
            // component's own `tool` state. Without this, the toolbar highlight and any
            // `tool === 'x'` hint stayed on whatever was active before the key was pressed,
            // even though the engine -- and a stroke drawn right after -- had switched.
            onToolChange={activateTool}
            showLabels={showLabels}
            showSuggestions={showSuggestions}
            fillOpacity={fillOpacity}
          />

          {tool === 'skeleton' && (
            <div
              className="pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 rounded-md border border-curve-500/40 bg-ink-900/90 px-3 py-1.5 text-xs text-ink-200 shadow-lg"
              data-skeleton-status=""
            >
              {skeletonHint(labelStyles, activeLabelId, toolStatus)}
            </div>
          )}

          {tool === 'brush' && (
            <div
              className="pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 rounded-md border border-curve-500/40 bg-ink-900/90 px-3 py-1.5 text-xs text-ink-200 shadow-lg"
              data-brush-status=""
            >
              {brushHint(toolStatus)}
            </div>
          )}

          {tool === 'cuboid' && (
            <div
              className="pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 rounded-md border border-curve-500/40 bg-ink-900/90 px-3 py-1.5 text-xs text-ink-200 shadow-lg"
              data-cuboid-status=""
            >
              {cuboidHint(toolStatus)}
            </div>
          )}

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
            labels={drawableLabels(labelStyles)}
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
            showLabels={showLabels}
            onToggleShowLabels={() => setShowLabels((current) => !current)}
            showSuggestions={showSuggestions}
            onToggleShowSuggestions={() => setShowSuggestions((current) => !current)}
            fillOpacity={fillOpacity}
            onFillOpacityChange={setFillOpacity}
          />

          <ObjectList
            annotations={visible}
            labels={labelStyles}
            selection={selection}
            onFocus={(id) => engine?.focusAnnotation(id)}
          />

          <AutoAnnotatePanel
            jobId={jobId}
            labels={drawableLabels(labelStyles)}
            // Predictions are annotations on the frame, so the canvas has to be told the
            // frame changed underneath it.
            onRan={() => void queryClient.invalidateQueries({ queryKey: ['annotations', jobId] })}
          />

          {/* Renders nothing unless this job holds suggestions nobody has ruled on, so it
              costs an ordinary hand-annotated job no space. */}
          <SuggestionsPanel
            jobId={jobId}
            document={annotations.data}
            onDecided={() =>
              void queryClient.invalidateQueries({ queryKey: ['annotations', jobId] })
            }
          />

          <IssuesPanel
            jobId={jobId}
            currentFrame={currentFrame}
            picking={picking}
            pickedPoint={pickedPoint}
            onPickingChange={setPicking}
            onClearPoint={() => {
              setPickedPoint(null);
              setPicking(false);
            }}
            onOpenThread={setOpenIssueId}
            // The object an issue would be about. Only a single selection anchors: "these
            // three boxes are wrong" is a different comment from "this one is", and the API
            // anchors an issue to one object.
            selected={
              selection.length === 1
                ? (visible.find((item) => item.id === selection[0]) ?? null)
                : null
            }
            onSeek={setFrame}
          />

          <QualityPanel job={job.data} labels={labelStyles} onSeek={setFrame} />

          {/* Last, because that is the order the decision is made in: read the objects, the
              issues and the score, then rule on the job. Renders nothing unless it is
              submitted, so an ordinary job being annotated pays no space for it. */}
          <ReviewPanel job={job.data} />

          <ShortcutHelp />
        </aside>
      </div>

      <TrackTimeline
        rows={tracks}
        labels={labelStyles}
        startFrame={job.data?.start_frame ?? 0}
        stopFrame={job.data?.stop_frame ?? 0}
        currentFrame={currentFrame}
        onSeek={setFrame}
        selectedTrackId={selectedTrackId}
        onSelectTrack={(trackId) => {
          setSelectedTrackId(trackId);
          setTrackNotice(null);
        }}
        onMoveKeyframe={(trackId, from, to) => {
          setTrackNotice(null);
          editTrack.mutate({ trackId, edit: (track) => moveKeyframe(track, from, to) });
        }}
        notice={trackNotice}
      />

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
  showLabels,
  onToggleShowLabels,
  showSuggestions,
  onToggleShowSuggestions,
  fillOpacity,
  onFillOpacityChange,
}: {
  labels: LabelStyle[];
  activeLabelId: string | null;
  onSelect: (id: string) => void;
  onToggleVisible: (id: string, visible: boolean) => void;
  onToggleLocked: (id: string, locked: boolean) => void;
  /** Every label at once, layered under each label's own toggle above. */
  showLabels: boolean;
  onToggleShowLabels: () => void;
  /** A suggestion nobody has reviewed yet -- an accepted one stays visible regardless. */
  showSuggestions: boolean;
  onToggleShowSuggestions: () => void;
  fillOpacity: number;
  onFillOpacityChange: (opacity: number) => void;
}) {
  return (
    <div className="border-b border-ink-800">
      <div className="flex items-center justify-between px-3 py-2">
        <h3 className="text-xs font-medium uppercase tracking-wide text-ink-500">Labels</h3>
        <div className="flex items-center gap-1">
          <button
            type="button"
            // `showLabels` only gates the little name/confidence chip drawn above each
            // shape (`Renderer.paintLabel`) -- the shapes themselves are unaffected, and
            // stay governed by each label's own visibility toggle in the list below.
            title={showLabels ? 'Hide label names on shapes' : 'Show label names on shapes'}
            onClick={onToggleShowLabels}
            className="rounded p-1 text-ink-500 hover:bg-ink-800 hover:text-ink-200"
            data-view-show-labels=""
          >
            {showLabels ? <Eye size={13} /> : <EyeOff size={13} />}
          </button>
          <button
            type="button"
            title={showSuggestions ? 'Hide unreviewed suggestions' : 'Show unreviewed suggestions'}
            onClick={onToggleShowSuggestions}
            className="rounded p-1 text-ink-500 hover:bg-ink-800 hover:text-ink-200"
            data-view-show-suggestions=""
          >
            <Wand2 size={13} className={showSuggestions ? undefined : 'opacity-40'} />
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2 px-3 pb-2">
        <label htmlFor="fill-opacity" className="text-[11px] text-ink-500">
          Fill
        </label>
        <input
          id="fill-opacity"
          type="range"
          min={0}
          max={1}
          step={0.02}
          value={fillOpacity}
          onChange={(event) => onFillOpacityChange(Number(event.target.value))}
          className="h-1 flex-1 cursor-pointer appearance-none rounded-full bg-ink-800 accent-curve-500"
          aria-label="Fill opacity"
          data-view-fill-opacity=""
        />
        <span className="w-8 text-right text-[11px] tabular-nums text-ink-500">
          {Math.round(fillOpacity * 100)}%
        </span>
      </div>

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
                data-label-id={label.id}
                data-label-name={label.name}
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
                // A stable hook for the browser harnesses, and the only thing that tells an
                // object row apart from a label row: both are `aside li button` carrying the
                // label's name, and selecting the wrong one silently selects nothing.
                data-object-id={annotation.id}
                // Selection state as a hook too: "the row looks highlighted" is a colour
                // class, which a harness cannot read without knowing the palette.
                data-selected={selection.includes(annotation.id) ? 'true' : 'false'}
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
                {/* Marks what still wants a decision, matching the dashed stroke on the
                    canvas. An accepted suggestion keeps `source = "model"` for provenance
                    but is an ordinary annotation, so it is not flagged here either. */}
                {isUnreviewed(annotation) && (
                  <Wand2
                    size={11}
                    className="text-amber-400"
                    aria-label="Awaiting review"
                    data-unreviewed=""
                  />
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
          ['S / J', 'Scissors, skeleton'],
          ['Space (hold)', 'Pan'],
          ['← / →', 'Previous / next frame'],
          [', / .', 'Previous / next keyframe'],
          ['K / O', 'Add or remove a keyframe / mark a departure'],
          ['Enter', 'Finish polygon or skeleton'],
          ['Backspace', 'Remove last vertex or joint'],
          ['X', 'Skip the joint being asked for'],
          ['Alt+click', 'Place a joint as occluded'],
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
