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

import { useCallback, useRef } from 'react';
import clsx from 'clsx';
import type { LabelStyle } from '@/canvas/types';
import { framePosition, type TrackRow } from './timeline';

const LANE_HEIGHT = 18;

export function TrackTimeline({
  rows,
  labels,
  startFrame,
  stopFrame,
  currentFrame,
  onSeek,
}: {
  rows: TrackRow[];
  labels: LabelStyle[];
  startFrame: number;
  stopFrame: number;
  currentFrame: number;
  onSeek: (frame: number) => void;
}) {
  const byId = new Map(labels.map((label) => [label.id, label]));

  if (rows.length === 0) return null;

  return (
    <div className="max-h-32 shrink-0 overflow-y-auto border-t border-ink-800 bg-ink-900/40">
      <div className="px-3 py-1.5">
        <h3 className="pb-1 text-[10px] font-medium uppercase tracking-wide text-ink-500">
          Tracks
        </h3>
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
}: {
  row: TrackRow;
  label: LabelStyle | undefined;
  startFrame: number;
  stopFrame: number;
  currentFrame: number;
  onSeek: (frame: number) => void;
}) {
  const bar = useRef<HTMLButtonElement>(null);
  const colour = label?.color ?? '#64748b';
  const name = label?.name ?? 'track';

  // Clicking the lane seeks to the frame under the pointer, which is how a timeline is
  // expected to behave and saves dragging the slider to reach a track's first appearance.
  const seekToPointer = useCallback(
    (clientX: number) => {
      const box = bar.current?.getBoundingClientRect();
      if (!box || box.width === 0) return;
      const fraction = Math.min(1, Math.max(0, (clientX - box.left) / box.width));
      onSeek(startFrame + Math.round(fraction * (stopFrame - startFrame)));
    },
    [onSeek, startFrame, stopFrame],
  );

  const percent = (frame: number) => `${framePosition(frame, startFrame, stopFrame) * 100}%`;

  return (
    <li className="flex items-center gap-2">
      <span
        className="h-2 w-2 shrink-0 rounded-sm"
        style={{ backgroundColor: colour }}
        aria-hidden
      />
      <span className="w-20 shrink-0 truncate text-[10px] text-ink-400" title={name}>
        {name}
      </span>

      <button
        ref={bar}
        type="button"
        onClick={(event) => seekToPointer(event.clientX)}
        className="relative flex-1 rounded-sm bg-ink-850 focus:outline-none focus-visible:ring-1 focus-visible:ring-curve-400"
        style={{ height: LANE_HEIGHT }}
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

        {/* Keyframes. A departure is drawn hollow: it marks where the object goes, not
            where it is, and conflating the two is the thing this whole file exists to
            avoid. */}
        {row.keyframes.map((frame) => {
          const departure = row.departures.includes(frame);
          return (
            <span
              key={frame}
              className={clsx(
                'absolute top-1/2 h-2 w-[3px] -translate-x-1/2 -translate-y-1/2 rounded-[1px]',
                departure && 'ring-1',
              )}
              style={
                departure
                  ? { left: percent(frame), backgroundColor: 'transparent', boxShadow: `inset 0 0 0 1px ${colour}` }
                  : { left: percent(frame), backgroundColor: colour }
              }
            />
          );
        })}

        {/* The playhead, so a lane reads against the frame you are actually on. */}
        <span
          className="absolute inset-y-0 w-px bg-ink-100/70"
          style={{ left: percent(currentFrame) }}
          aria-hidden
        />
      </button>
    </li>
  );
}
