/**
 * The two pure decisions behind the upload panel: which files are worth the resumable
 * protocol's overhead, and what to tell the user once a batch has run.
 *
 * `recallUploadId`/`rememberUploadId`/`uploadLargeFileResumable` touch `localStorage` and
 * the network respectively, so — like `chooseFiles`/`localImport` elsewhere in this
 * directory — they are verified by `scripts/verify_resumable_upload.py` against the real
 * packaged application rather than mocked here.
 */

import { describe, expect, it } from 'vitest';
import {
  RESUMABLE_UPLOAD_THRESHOLD_BYTES,
  partitionBySize,
  summariseUpload,
  type UploadEntry,
} from '../resumableUpload';

function fileOfSize(name: string, size: number): File {
  return new File([new Uint8Array(size)], name);
}

describe('partitionBySize', () => {
  it('keeps a handful of ordinary photos in the small batch', () => {
    const files = [fileOfSize('a.jpg', 1024), fileOfSize('b.jpg', 2048)];
    const { small, large } = partitionBySize(files);
    expect(small.map((f) => f.name)).toEqual(['a.jpg', 'b.jpg']);
    expect(large).toEqual([]);
  });

  it('routes a file at or above the threshold to the resumable path', () => {
    const files = [
      fileOfSize('clip.mp4', RESUMABLE_UPLOAD_THRESHOLD_BYTES),
      fileOfSize('photo.jpg', RESUMABLE_UPLOAD_THRESHOLD_BYTES - 1),
    ];
    const { small, large } = partitionBySize(files);
    expect(large.map((f) => f.name)).toEqual(['clip.mp4']);
    expect(small.map((f) => f.name)).toEqual(['photo.jpg']);
  });

  it('honours a custom threshold, so the default is not hard-coded into callers', () => {
    const files = [fileOfSize('a.jpg', 500)];
    expect(partitionBySize(files, 100).large).toHaveLength(1);
    expect(partitionBySize(files, 1000).small).toHaveLength(1);
  });

  it('splits a mixed batch into both groups', () => {
    const files = [
      fileOfSize('a.jpg', 1000),
      fileOfSize('big.mp4', RESUMABLE_UPLOAD_THRESHOLD_BYTES * 2),
      fileOfSize('b.jpg', 2000),
    ];
    const { small, large } = partitionBySize(files);
    expect(small.map((f) => f.name)).toEqual(['a.jpg', 'b.jpg']);
    expect(large.map((f) => f.name)).toEqual(['big.mp4']);
  });
});

describe('summariseUpload', () => {
  function entry(name: string, ok: boolean, reason?: string): UploadEntry {
    return { name, ok, reason };
  }

  it('reports a clean batch as a success', () => {
    const summary = summariseUpload([entry('a.jpg', true), entry('b.jpg', true)]);
    expect(summary.tone).toBe('success');
    expect(summary.headline).toBe('Uploaded 2 files.');
  });

  it('never calls a partial batch a success', () => {
    const summary = summariseUpload([
      entry('a.jpg', true),
      entry('clip.mp4', false, 'connection reset'),
    ]);
    expect(summary.tone).toBe('warning');
    expect(summary.headline).toBe('Uploaded 1 file; 1 file failed.');
  });

  it('says every file failed, not "uploaded 0 files"', () => {
    const summary = summariseUpload([entry('clip.mp4', false, 'connection reset')]);
    expect(summary.tone).toBe('warning');
    expect(summary.headline).toBe('1 file failed to upload.');
  });

  it('handles nothing selected without claiming a batch happened', () => {
    const summary = summariseUpload([]);
    expect(summary.tone).toBe('warning');
    expect(summary.headline).toBe('No files selected.');
  });

  it('gets singulars right', () => {
    expect(summariseUpload([entry('a.jpg', true)]).headline).toBe('Uploaded 1 file.');
  });

  it('groups thousands', () => {
    const entries = Array.from({ length: 1200 }, (_, index) => entry(`${index}.jpg`, true));
    expect(summariseUpload(entries).headline).toBe('Uploaded 1,200 files.');
  });
});
