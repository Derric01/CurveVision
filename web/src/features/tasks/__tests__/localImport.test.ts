/**
 * What the user is told after importing a folder.
 *
 * The server deliberately does not fail a whole import over one unreadable file, which
 * means a partial result is the normal case, not the exception. These tests exist to stop
 * the partial case from being reported as a clean success — the specific dishonesty this
 * screen could commit.
 */

import { describe, expect, it } from 'vitest';
import { summariseImport } from '../localImport';
import type { Asset, LocalImportResult } from '@/api/types';

function asset(name: string): Asset {
  return {
    id: `id-${name}`,
    task_id: 'task-1',
    name,
    position: 0,
    start_frame: 0,
    frame_count: 1,
    created_at: '2026-09-12T00:00:00Z',
  };
}

function result(over: Partial<LocalImportResult> = {}): LocalImportResult {
  return {
    task_id: 'task-1',
    imported: [asset('a.jpg')],
    skipped: [],
    frame_count: 1,
    ...over,
  };
}

describe('summariseImport', () => {
  it('reports a clean import as a success, with the new frame count', () => {
    const summary = summariseImport(
      result({ imported: [asset('a.jpg'), asset('b.jpg')], frame_count: 2 }),
    );

    expect(summary.tone).toBe('success');
    expect(summary.headline).toBe('Imported 2 files. This task now has 2 frames.');
    expect(summary.skipped).toEqual([]);
  });

  it('never calls a partial import a success', () => {
    const summary = summariseImport(
      result({
        imported: [asset('a.jpg')],
        skipped: ['broken.jpg: cannot identify image file'],
        frame_count: 1,
      }),
    );

    expect(summary.tone).toBe('warning');
    expect(summary.headline).toContain('skipped 1 file');
    expect(summary.skipped).toEqual(['broken.jpg: cannot identify image file']);
  });

  it('says why nothing was imported, rather than only that nothing was', () => {
    const summary = summariseImport(
      result({
        imported: [],
        skipped: ['IMG_1.heic: unsupported', 'IMG_2.heic: unsupported'],
        frame_count: 0,
      }),
    );

    expect(summary.tone).toBe('warning');
    expect(summary.headline).toBe('Nothing was imported. 2 files could not be read.');
  });

  it('distinguishes an empty folder from a folder of unreadable files', () => {
    const summary = summariseImport(result({ imported: [], skipped: [], frame_count: 0 }));

    expect(summary.headline).toBe('No media found in that folder.');
    expect(summary.tone).toBe('warning');
  });

  it('gets singulars right, because "1 files" reads as a bug', () => {
    const summary = summariseImport(result({ imported: [asset('a.jpg')], frame_count: 1 }));
    expect(summary.headline).toBe('Imported 1 file. This task now has 1 frame.');
  });

  it('groups thousands, because a folder import is often large', () => {
    const imported = Array.from({ length: 1200 }, (_, index) => asset(`${index}.jpg`));
    const summary = summariseImport(result({ imported, frame_count: 1200 }));

    expect(summary.headline).toContain('1,200 files');
    expect(summary.headline).toContain('1,200 frames');
  });
});
