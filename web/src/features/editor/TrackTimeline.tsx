/**
 * Where every track in this job lives along its frame range.
 *
 * Without this, a track is invisible until you happen to scrub onto a frame it occupies:
 * the canvas shows what is on *this* frame and nothing tells you an object exists two
 * hundred frames away, or that the one you are looking at disappears in six. That is the
 * gap between "you can annotate video" and "you would want to".
 *
 * Presence comes from `timeline.ts`, which derives it from the keyframes with the same
 * rules `interpolateTrack` uses — including the one that matters, that an `outside`
 * keyframe is the object *leaving* and a track can come back afterwards. A bar drawn from
 * first keyframe to last would claim the object is on screen during its absences.
 */

import { useCallback, useRef, useState } from 'react';
import clsx from 'clsx';
import type { LabelStyle } from '@/canvas/types';
import { frameAtPosition, framePosition, type TrackRow } from './timeline';

const LANE_HEIGHT = 18;

export function TrackTimeline({
  rows,
  labels,
  startFrame,
  stopFrame,
  currentFrame,
  onSeek,
  selectedTrackId,
  onSelectTrack,
  onMoveKeyframe,
  notice,
}: {
  rows: TrackRow[];
  labels: LabelStyle[];
  startFrame: number;
  stopFrame: number;
  currentFrame: number;
  onSeek: (frame: number) => void;
  /** The track keyframe shortcuts act on; `null` when none is chosen. */
  selectedTrackId?: string | null;
  onSelectTrack?: (trackId: string | null) => void;
  /** Move a keyframe to another frame. Omitted, the markers are not draggable. */
  onMoveKeyframe?: (trackId: string, from: number, to: number) => void;
  /** Why the last keyframe edit did nothing, when it did nothing. */
  notice?: string | null;
}) {
  const byId = new Map(labels.map((label) => [label.id, label]));

  if (rows.length === 0) return null;

  return (
    <div className="max-h-32 shrink-0 overflow-y-auto border-t border-ink-800 bg-ink-900/40">
      <div className="px-3 py-1.5">
        <div className="flex items-baseline justify-between pb-1">
          <h3 className="text-[10px] font-medium uppercase tracking-wide text-ink-500">
            Tracks
          </h3>
          {onSelectTrack ? (
            <p className="text-[10px] text-ink-500">
              {selectedTrackId ? (
                <>
                  <kbd className="rounded bg-ink-800 px-1">K</kbd> keyframe ·{' '}
                  <kbd className="rounded bg-ink-800 px-1">O</kbd> leaves here
                  {onMoveKeyframe ? ' · drag a marker to move it' : null}
                </>
              ) : (
                'Select a track to edit its keyframes'
              )}
            </p>
          ) : null}
        </div>
        {notice ? (
          <p className="mb-1 rounded-sm bg-amber-500/10 px-2 py-1 text-[10px] text-amber-300">
            {notice}
          </p>
        ) : null}
        <ul className="space-y-0.5">
          {rows.map((row) => (
            <Lane
              key={row.trackId}
              row={row}
              label={byId.get(row.labelId)}
              startFrame={startFrame}
              stopFrame={stopFrame}
              currentFrame={currentFrame}
              onSeek={onSeek}
              selected={selectedTrackId === row.trackId}
              onSelect={onSelectTrack}
              onMoveKeyframe={onMoveKeyframe}
            />
          ))}
        </ul>
      </div>
    </div>
  );
}

function Lane({
  row,
  label,
  startFrame,
  stopFrame,
  currentFrame,
  onSeek,
  selected,
  onSelect,
  onMoveKeyframe,
}: {
  row: TrackRow;
  label: LabelStyle | undefined;
  startFrame: number;
  stopFrame: number;
  currentFrame: number;
  onSeek: (frame: number) => void;
  selected?: boolean;
  onSelect?: (trackId: string | null) => void;
  onMoveKeyframe?: (trackId: string, from: number, to: number) => void;
}) {
  const bar = useRef<HTMLButtonElement>(null);
  const colour = label?.color ?? '#64748b';
  const name = label?.name ?? 'track';

  // The keyframe being dragged, and where it currently sits. Local state rather than a
  // write per pointer move: a drag across a 600-frame lane crosses hundreds of frames, and
  // a write each would be hundreds of round trips for one edit the annotator has not
  // finished making. The move is sent once, on release.
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null);
  /** The frame under a pointer x, in the lane's own coordinates. */
  const frameAtPointer = useCallback(
    (clientX: number): number | null => {
      const box = bar.current?.getBoundingClientRect();
      if (!box || box.width === 0) return null;
      return frameAtPosition((clientX - box.left) / box.width, startFrame, stopFrame);
    },
    [startFrame, stopFrame],
  );

  // Clicking the lane seeks to the frame under the pointer, which is how a timeline is
  // expected to behave and saves dragging the slider to reach a track's first appearance.
  const seekToPointer = useCallback(
    (clientX: number) => {
      const frame = frameAtPointer(clientX);
      if (frame !== null) onSeek(frame);
    },
    [frameAtPointer, onSeek],
  );

  const startDrag = useCallback(
    (event: React.PointerEvent, frame: number) => {
      if (!onMoveKeyframe) return;
      // Keep the lane's own click-to-seek out of it, and take the pointer so the drag
      // survives leaving the 18px lane vertically — which it will, constantly.
      event.stopPropagation();
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      setDrag({ from: frame, to: frame });
      // Dragging a lane's keyframe is working on that track, so select it. Otherwise the
      // annotator drags a marker and the `K`/`O` shortcuts still act on a different track.
      if (onSelect && !selected) onSelect(row.trackId);
    },
    [onMoveKeyframe, onSelect, selected, row.trackId],
  );

  const moveDrag = useCallback(
    (event: React.PointerEvent) => {
      if (!drag) return;
      const frame = frameAtPointer(event.clientX);
      if (frame === null || frame === drag.to) return;
      setDrag({ from: drag.from, to: frame });
    },
    [drag, frameAtPointer],
  );

  const endDrag = useCallback(
    (event: React.PointerEvent) => {
      if (!drag) return;
      event.stopPropagation();
      const { from, to } = drag;
      setDrag(null);
      // A press that never moved is a click, and this is where that is decided — not in a
      // click handler. A click handler cannot tell the two apart without a flag saying "a
      // drag just happened", and a flag that the click is supposed to consume gets stuck
      // set whenever the click does not arrive. Here the pointer positions say it outright.
      if (to === from) onSeek(from);
      else onMoveKeyframe?.(row.trackId, from, to);
    },
    [drag, onMoveKeyframe, onSeek, row.trackId],
  );

  const percent = (frame: number) => `${framePosition(frame, startFrame, stopFrame) * 100}%`;

  return (
    <li className="flex items-center gap-2">
      <span
        className="h-2 w-2 shrink-0 rounded-sm"
        style={{ backgroundColor: colour }}
        aria-hidden
      />
      {onSelect ? (
        <button
          type="button"
          onClick={() => onSelect(selected ? null : row.trackId)}
          aria-pressed={selected}
          // A stable hook for the browser harnesses. `aria-pressed` alone stopped being
          // unique once the quality panel added filter chips, and a harness that finds
          // "the first toggle in the editor" is one layout change from driving the wrong
          // button.
          data-track-selector=""
          title={`${name} — click to ${selected ? 'deselect' : 'select for keyframe editing'}`}
          className={clsx(
            'w-20 shrink-0 truncate rounded-sm px-1 text-left text-[10px]',
            selected ? 'bg-curve-500/20 text-curve-200' : 'text-ink-400 hover:text-ink-200',
          )}
        >
          {name}
        </button>
      ) : (
        <span className="w-20 shrink-0 truncate text-[10px] text-ink-400" title={name}>
          {name}
        </span>
      )}

      {/* The lane is a positioned wrapper, and the keyframe markers are siblings of the
          seek button rather than children of it.

          That is structural, not cosmetic. When a marker sat inside the button, a drag
          ending on it still produced a `click` that bubbled to the button, so releasing a
          keyframe also seeked the playhead to wherever it was dropped. Suppressing that
          click with a flag worked until the click did not arrive — then the flag stayed
          set and swallowed the next real seek instead. Outside the button there is no
          click to suppress. */}
      <div className="relative flex-1" style={{ height: LANE_HEIGHT }}>
        <button
          ref={bar}
          type="button"
          onClick={(event) => seekToPointer(event.clientX)}
          className={clsx(
            'absolute inset-0 rounded-sm focus:outline-none focus-visible:ring-1 focus-visible:ring-curve-400',
            selected ? 'bg-ink-800 ring-1 ring-curve-500/40' : 'bg-ink-850',
          )}
          aria-label={`${name}: ${row.keyframes.length} keyframes. Click to seek.`}
        >
          {/* Presence. One bar per appearance, so absences read as gaps. */}
          {row.segments.map((segment) => (
            <span
              key={`${segment.start}-${segment.stop}`}
              className="absolute inset-y-1 rounded-sm"
              style={{
                left: percent(segment.start),
                // +1 frame of width so a one-frame segment is still visible rather than zero-wide.
                width: `calc(${percent(segment.stop)} - ${percent(segment.start)} + 2px)`,
                backgroundColor: colour,
                opacity: 0.35,
              }}
            />
          ))}

          {/* The playhead, so a lane reads against the frame you are actually on. */}
          <span
            className="absolute inset-y-0 w-px bg-ink-100/70"
            style={{ left: percent(currentFrame) }}
            aria-hidden
          />
        </button>

        {/* Keyframes. A departure is drawn hollow: it marks where the object goes, not
            where it is, and conflating the two is the thing this whole file exists to
            avoid. */}
        {row.keyframes.map((frame) => {
          const departure = row.departures.includes(frame);
          // While dragging, the marker is drawn where the pointer is rather than where the
          // keyframe is stored. That preview is the only thing telling the annotator which
          // frame they are about to drop on; a marker that stays put until the write lands
          // makes the drag a guess.
          const dragging = drag?.from === frame;
          const shown = dragging ? drag.to : frame;
          const label = `Frame ${frame}${departure ? ' — object leaves' : ''}`;
          return (
            <button
              key={frame}
              type="button"
              // The visible marker is 3px wide, which is a target nobody can hit. This
              // wrapper is 11px of transparent padding around it, so the hit area grows
              // without the lane looking different.
              onPointerDown={(event) => startDrag(event, frame)}
              onPointerMove={moveDrag}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              // Keyboard only. `detail === 0` means no pointer was involved — Enter or
              // Space on a focused marker — so a mouse click does not reach here twice:
              // `endDrag` has already handled the pointer case.
              onClick={(event) => {
                if (event.detail === 0) onSeek(frame);
              }}
              data-keyframe={frame}
              title={onMoveKeyframe ? `${label}. Drag to move it.` : label}
              aria-label={onMoveKeyframe ? `${label}. Drag to move it.` : label}
              className={clsx(
                'absolute top-1/2 z-10 flex h-4 w-[11px] -translate-x-1/2 -translate-y-1/2',
                'items-center justify-center focus:outline-none',
                'focus-visible:ring-1 focus-visible:ring-curve-400',
                // Without a move handler there is nothing to drag, and an 11px button that
                // swallows clicks would make the lane *less* clickable than it was before
                // markers became interactive. Let the pointer through to the lane instead.
                onMoveKeyframe
                  ? dragging
                    ? 'cursor-grabbing'
                    : 'cursor-grab'
                  : 'pointer-events-none',
              )}
              style={{ left: percent(shown) }}
            >
              <span
                className={clsx('h-2 w-[3px] rounded-[1px]', dragging && 'h-3 ring-1 ring-ink-100')}
                style={
                  departure
                    ? { backgroundColor: 'transparent', boxShadow: `inset 0 0 0 1px ${colour}` }
                    : { backgroundColor: colour }
                }
              />
            </button>
          );
        })}
      </div>
    </li>
  );
}
