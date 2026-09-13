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
  selectedTrackId,
  onSelectTrack,
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
}: {
  row: TrackRow;
  label: LabelStyle | undefined;
  startFrame: number;
  stopFrame: number;
  currentFrame: number;
  onSeek: (frame: number) => void;
  selected?: boolean;
  onSelect?: (trackId: string | null) => void;
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
      {onSelect ? (
        <button
          type="button"
          onClick={() => onSelect(selected ? null : row.trackId)}
          aria-pressed={selected}
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

      <button
        ref={bar}
        type="button"
        onClick={(event) => seekToPointer(event.clientX)}
        className={clsx(
          'relative flex-1 rounded-sm focus:outline-none focus-visible:ring-1 focus-visible:ring-curve-400',
          selected ? 'bg-ink-800 ring-1 ring-curve-500/40' : 'bg-ink-850',
        )}
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
