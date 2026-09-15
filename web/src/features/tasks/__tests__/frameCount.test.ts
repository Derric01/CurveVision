import { describe, expect, it } from 'vitest';
import type { TaskMediaMeta } from '@/api/types';
import {
  describeFiles,
  frameCountWarning,
  recountDisabled,
  recountLabel,
  recountNote,
} from '../frameCount';

function meta(overrides: Partial<TaskMediaMeta> = {}): TaskMediaMeta {
  return {
    task_id: 't1',
    media_kind: 'video',
    frame_count: 1500,
    frames_per_chunk: 36,
    chunk_count: 42,
    ...overrides,
  };
}

/** `frameCountWarning` returns null often and legitimately; this asserts the other case. */
function warningFor(m: TaskMediaMeta | undefined | null) {
  const warning = frameCountWarning(m);
  if (!warning) throw new Error('expected a warning');
  return warning;
}

describe('frameCountWarning', () => {
  it('says nothing when the count was established by decoding', () => {
    expect(frameCountWarning(meta({ frame_count_exact: true }))).toBeNull();
  });

  it('says nothing for a task of images', () => {
    expect(
      frameCountWarning(meta({ media_kind: 'image', frame_count_exact: true })),
    ).toBeNull();
  });

  it('says nothing when there is no metadata yet', () => {
    expect(frameCountWarning(undefined)).toBeNull();
    expect(frameCountWarning(null)).toBeNull();
  });

  // The field is optional because an older server omits it. Absence has to mean "fine",
  // not "warn": the alternative puts a scary notice on every task the moment the client
  // is deployed ahead of the server.
  it('treats a missing field as trustworthy rather than as a warning', () => {
    expect(frameCountWarning(meta())).toBeNull();
  });

  it('warns when an asset is still an estimate', () => {
    const warning = warningFor(
      meta({ frame_count_exact: false, estimated_assets: ['clip.mp4'], estimated_asset_count: 1 }),
    );
    expect(warning.headline).toBe("This task's frame count is an estimate");
    expect(warning.files).toEqual(['clip.mp4']);
    expect(warning.total).toBe(1);
    expect(warning.unnamed).toBe(0);
  });

  it('counts the files in the headline when there is more than one', () => {
    const warning = warningFor(
      meta({
        frame_count_exact: false,
        estimated_assets: ['a.mp4', 'b.mp4'],
        estimated_asset_count: 2,
      }),
    );
    expect(warning.headline).toBe('2 files have an estimated frame count');
  });

  it('quotes the task frame count in the detail, formatted', () => {
    const warning = warningFor(meta({ frame_count: 1500, frame_count_exact: false }));
    expect(warning.detail).toContain((1500).toLocaleString());
  });

  it('reports how many estimated assets were not named', () => {
    const warning = warningFor(
      meta({
        frame_count_exact: false,
        estimated_assets: ['a.mp4', 'b.mp4', 'c.mp4', 'd.mp4', 'e.mp4'],
        estimated_asset_count: 12,
      }),
    );
    expect(warning.total).toBe(12);
    expect(warning.unnamed).toBe(7);
  });

  // The server caps the sample; if the two disagree the names are the thing we can see.
  it('never claims fewer files than it was given names for', () => {
    const warning = warningFor(
      meta({
        frame_count_exact: false,
        estimated_assets: ['a.mp4', 'b.mp4', 'c.mp4'],
        estimated_asset_count: 1,
      }),
    );
    expect(warning.total).toBe(3);
    expect(warning.unnamed).toBe(0);
  });

  // `frame_count_exact: false` is the server saying something *is* estimated, so a missing
  // sample means "we did not name it", not "there is nothing". One unnamed file is the
  // honest floor.
  it('still warns when the server named no files at all', () => {
    const warning = warningFor(meta({ frame_count_exact: false }));
    expect(warning.files).toEqual([]);
    expect(warning.total).toBe(1);
    expect(warning.unnamed).toBe(1);
    expect(warning.headline).toBe("This task's frame count is an estimate");
  });
});

describe('describeFiles', () => {
  it('is empty when there is nothing to name', () => {
    expect(describeFiles(warningFor(meta({ frame_count_exact: false })))).toBe('');
  });

  it('names one file plainly', () => {
    const warning = warningFor(
      meta({ frame_count_exact: false, estimated_assets: ['clip.mp4'], estimated_asset_count: 1 }),
    );
    expect(describeFiles(warning)).toBe('clip.mp4');
  });

  it('joins two files with "and", not a comma', () => {
    const warning = warningFor(
      meta({
        frame_count_exact: false,
        estimated_assets: ['a.mp4', 'b.mp4'],
        estimated_asset_count: 2,
      }),
    );
    expect(describeFiles(warning)).toBe('a.mp4 and b.mp4');
  });

  it('uses commas then "and" for three', () => {
    const warning = warningFor(
      meta({
        frame_count_exact: false,
        estimated_assets: ['a.mp4', 'b.mp4', 'c.mp4'],
        estimated_asset_count: 3,
      }),
    );
    expect(describeFiles(warning)).toBe('a.mp4, b.mp4 and c.mp4');
  });

  it('folds the unnamed remainder into the last position', () => {
    const warning = warningFor(
      meta({
        frame_count_exact: false,
        estimated_assets: ['a.mp4', 'b.mp4'],
        estimated_asset_count: 5,
      }),
    );
    expect(describeFiles(warning)).toBe('a.mp4, b.mp4 and 3 more');
  });

  it('reads correctly when only one file was named out of many', () => {
    const warning = warningFor(
      meta({
        frame_count_exact: false,
        estimated_assets: ['a.mp4'],
        estimated_asset_count: 2,
      }),
    );
    expect(describeFiles(warning)).toBe('a.mp4 and 1 more');
  });
});

describe('the recount button', () => {
  it('offers to recount when idle', () => {
    expect(recountLabel('idle')).toBe('Recount frames');
    expect(recountDisabled('idle')).toBe(false);
  });

  it('is disabled while the request is in flight', () => {
    expect(recountLabel('working')).toBe('Starting…');
    expect(recountDisabled('working')).toBe(true);
  });

  // A second press queues a second decode of the same file, which is exactly what someone
  // does when a button looks ready and nothing visible has changed yet.
  it('stays disabled once the job is queued', () => {
    expect(recountLabel('queued')).toBe('Counting…');
    expect(recountDisabled('queued')).toBe(true);
  });

  it('can be pressed again after a failure', () => {
    expect(recountLabel('failed')).toBe('Recount frames');
    expect(recountDisabled('failed')).toBe(false);
  });

  it('explains what is happening only once something is happening', () => {
    expect(recountNote('idle')).toBeNull();
    expect(recountNote('working')).toBeNull();
    expect(recountNote('failed')).toBeNull();
    expect(recountNote('queued')).toContain('decodes the whole video');
  });

  // Nothing here can know how long a decode takes, so nothing here should say.
  it('does not promise a duration', () => {
    const note = recountNote('queued') ?? '';
    expect(note).not.toMatch(/minute|second|hour/i);
  });
});
