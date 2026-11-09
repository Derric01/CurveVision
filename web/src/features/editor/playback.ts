/**
 * Playing a job's frames in order, like a video.
 *
 * The editor could only step: an arrow per frame, or the slider. Watching an object move
 * is how an annotator finds where a track drifts, and every video tool has a play button.
 *
 * The next frame is asked for only once the current one is on screen (its image URL has
 * arrived), so playback never runs ahead of loading: on a slow connection it plays slower
 * rather than flashing blank frames. It stops at the job's last frame, and the editor
 * pauses it as soon as the annotator steps, scrubs or touches the canvas.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

/** Frames per second the annotator can choose from. */
export const PLAYBACK_RATES = [1, 5, 10, 25] as const;
export type PlaybackRate = (typeof PLAYBACK_RATES)[number];
export const DEFAULT_PLAYBACK_RATE: PlaybackRate = 10;

/** The frame after `current`, or null at the end of the job. */
export function nextPlaybackFrame(current: number, stop: number): number | null {
  return current < stop ? current + 1 : null;
}

export function usePlayback({
  frame,
  start,
  stop,
  shown,
  rate,
  onFrame,
}: {
  /** The frame on screen now. */
  frame: number;
  start: number;
  stop: number;
  /** The current frame's image, once it has loaded; null while it is loading. */
  shown: string | null;
  rate: PlaybackRate;
  onFrame: (frame: number) => void;
}): { playing: boolean; play: () => void; pause: () => void } {
  const [playing, setPlaying] = useState(false);
  // Read when the timer fires, not captured when it was set, so a frame the annotator
  // chose in between is where playback continues from.
  const latest = useRef({ frame, stop, onFrame });
  latest.current = { frame, stop, onFrame };

  const pause = useCallback(() => setPlaying(false), []);
  const play = useCallback(() => {
    // From the end, play starts again at the beginning, as a video player does.
    if (latest.current.frame >= latest.current.stop) latest.current.onFrame(start);
    setPlaying(true);
  }, [start]);

  // Keyed on the image, not the frame number: a new frame number arrives before its
  // picture does, and stepping on then would skip frames nobody saw.
  useEffect(() => {
    if (!playing || shown === null) return;
    const timer = window.setTimeout(() => {
      const next = nextPlaybackFrame(latest.current.frame, latest.current.stop);
      if (next === null) setPlaying(false);
      else latest.current.onFrame(next);
    }, 1000 / rate);
    return () => window.clearTimeout(timer);
  }, [playing, shown, rate]);

  return { playing, play, pause };
}
