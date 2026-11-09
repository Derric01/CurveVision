import { describe, expect, it } from 'vitest';
import type { BackgroundTaskBrief } from '@/api/types';
import { runBackgroundExport } from '../backgroundExport';

const task = (state: string, extra: Partial<BackgroundTaskBrief> = {}): BackgroundTaskBrief => ({
  id: 't1',
  kind: 'dataset.export',
  state,
  progress: 0,
  message: null,
  error: null,
  ...extra,
});

describe('runBackgroundExport', () => {
  it('waits for the export, then hands its link to the download manager', async () => {
    const states = ['running', 'running', 'succeeded'];
    const downloaded: string[] = [];
    const seen: string[] = [];
    await runBackgroundExport({
      start: async () => task('pending'),
      poll: async () => task(states.shift()!),
      link: async () => ({ url: '/api/v1/exports/t1/archive?expires=1&signature=s' }),
      download: (url) => downloaded.push(url),
      wait: async () => {},
      onProgress: (t) => seen.push(t.state),
    });
    expect(seen).toEqual(['pending', 'running', 'running', 'succeeded']);
    expect(downloaded).toEqual(['/api/v1/exports/t1/archive?expires=1&signature=s']);
  });

  it("reports the server's reason when the export fails, and downloads nothing", async () => {
    const downloaded: string[] = [];
    await expect(
      runBackgroundExport({
        start: async () => task('pending'),
        poll: async () => task('failed', { error: 'No space left on device' }),
        link: async () => ({ url: 'x' }),
        download: (url) => downloaded.push(url),
        wait: async () => {},
      }),
    ).rejects.toThrow('No space left on device');
    expect(downloaded).toEqual([]);
  });
});
