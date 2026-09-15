/**
 * Whether a task's frame count can be trusted, and how to say so.
 *
 * A video task is created with an *estimated* frame count, because an exact one means
 * decoding the whole file and that cannot happen inside an upload request. A background job
 * normally replaces the estimate within seconds of upload. It can also decline — the task
 * already carries annotations, so its frame ranges are not the job's to move — or fail, on a
 * file that moved or a codec the build cannot decode. In both cases the task keeps a number
 * that may overstate the media, and an annotator who steps onto a frame past the real end
 * meets what looks like missing media.
 *
 * The server now records which assets were actually counted. This turns that into something
 * a person can read and act on. Pure functions, no React: the interesting decisions are
 * about wording and edge cases, and those are worth testing without a DOM.
 */

import type { TaskMediaMeta } from '@/api/types';

export interface FrameCountWarning {
  /** One line, safe to use as a heading. */
  headline: string;
  /** Two or three sentences: what the number is, and what to do about it. */
  detail: string;
  /** The filenames the server named, in position order. May be empty. */
  files: string[];
  /** How many assets are estimated in total — never fewer than `files.length`. */
  total: number;
  /** How many estimated assets the server did not name. Zero when `files` is complete. */
  unnamed: number;
}

/**
 * The warning to show for this task, or `null` when the frame count is trustworthy.
 *
 * Absent metadata reads as trustworthy rather than as a warning. The three fields are
 * optional because a server older than them omits them, and an old server's silence means
 * "nothing was ever estimated here" — inventing a warning from a missing field would put a
 * scary notice on every task the moment a client outran its server.
 */
export function frameCountWarning(meta: TaskMediaMeta | undefined | null): FrameCountWarning | null {
  if (!meta) return null;
  if (meta.frame_count_exact !== false) return null;

  const files = meta.estimated_assets ?? [];
  // `estimated_asset_count` is the truth; the names are a sample of it. Where the count is
  // missing or nonsensically small, the names we hold are the better floor — a warning that
  // lists three files and claims there is one is worse than no count at all.
  const total = Math.max(files.length, meta.estimated_asset_count ?? 0, 1);
  const unnamed = Math.max(0, total - files.length);

  return {
    headline:
      total === 1
        ? "This task's frame count is an estimate"
        : `${total} files have an estimated frame count`,
    detail:
      `${meta.frame_count.toLocaleString()} frames is what the container metadata claimed, ` +
      'not a count of the frames in the file. Metadata runs high on variable-frame-rate ' +
      'video, and when it does the last frames of the task do not exist — an annotator who ' +
      'reaches them sees what looks like missing media. Recounting decodes the video and ' +
      'replaces the estimate with the real number.',
    files,
    total,
    unnamed,
  };
}

/**
 * "clip.mp4", "clip.mp4 and b.mp4", "a.mp4, b.mp4 and 3 more".
 *
 * Returns an empty string when there is nothing to name, so a caller can render the warning
 * without the file line rather than an awkward dangling label.
 */
export function describeFiles(warning: FrameCountWarning): string {
  const { files, unnamed } = warning;
  if (files.length === 0) return '';

  const parts = [...files];
  if (unnamed > 0) parts.push(`${unnamed} more`);
  if (parts.length === 1) return parts[0] as string;

  const last = parts[parts.length - 1] as string;
  return `${parts.slice(0, -1).join(', ')} and ${last}`;
}

export type RecountPhase = 'idle' | 'working' | 'queued' | 'failed';

/**
 * What the recount button should say and whether it can be pressed.
 *
 * `queued` deliberately does not re-enable the button. The job takes as long as decoding
 * takes, and a second press would queue a second decode of the same file — which is the
 * kind of thing a person does when a button looks ready and nothing has visibly changed.
 * The count arriving is what clears it, via a refetch of the metadata.
 */
export function recountLabel(phase: RecountPhase): string {
  switch (phase) {
    case 'working':
      return 'Starting…';
    case 'queued':
      return 'Counting…';
    default:
      return 'Recount frames';
  }
}

export function recountDisabled(phase: RecountPhase): boolean {
  return phase === 'working' || phase === 'queued';
}

/**
 * What to tell someone after the job has been accepted.
 *
 * Deliberately does not promise a duration: the honest answer is "as long as decoding the
 * video takes", which depends on the file, and a made-up "about a minute" would be a claim
 * nothing here can support.
 */
export function recountNote(phase: RecountPhase): string | null {
  if (phase !== 'queued') return null;
  return (
    'Counting started. It decodes the whole video, so how long it takes depends on the ' +
    'file; this page picks up the new count when it lands.'
  );
}
