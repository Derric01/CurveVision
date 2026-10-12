import { describe, expect, it } from 'vitest';
import type { DatasetVersion } from '@/api/types';
import {
  canDeleteDraft,
  canRelease,
  describeVersion,
  labelCounts,
  shortHash,
  versionFilename,
  versionNameProblem,
} from '../versions';

function version(overrides: Partial<DatasetVersion> = {}): DatasetVersion {
  return {
    id: 'v',
    project_id: 'p',
    name: 'v1.0',
    description: null,
    state: 'released',
    content_hash: 'abcdef0123456789abcdef',
    statistics: {
      frame_count: 1200,
      shape_count: 5300,
      frames_without_annotations: 14,
      label_distribution: { car: 4000, person: 1300 },
    },
    created_at: '2026-10-01T00:00:00Z',
    released_at: '2026-10-02T00:00:00Z',
    ...overrides,
  };
}

describe('who may act', () => {
  it('mirrors the policy engine', () => {
    expect(canRelease('reviewer')).toBe(true);
    expect(canRelease('annotator')).toBe(false);
    expect(canDeleteDraft('maintainer')).toBe(true);
    expect(canDeleteDraft('reviewer')).toBe(false);
  });
});

describe('naming a version', () => {
  it('refuses an empty, overlong or taken name', () => {
    expect(versionNameProblem('  ', [])).toMatch(/Name the version/);
    expect(versionNameProblem('x'.repeat(121), [])).toMatch(/120/);
    expect(versionNameProblem(' v1.0 ', [version()])).toMatch(/already a version named “v1.0”/);
    expect(versionNameProblem('v1.1', [version()])).toBeNull();
  });
});

describe('describing a version', () => {
  it('says what a release holds, and how many frames hold nothing', () => {
    expect(describeVersion(version())).toBe(
      `${(1200).toLocaleString()} frames · ${(5300).toLocaleString()} annotations · 14 frames with none`,
    );
    expect(describeVersion(version({ statistics: { frame_count: 2, shape_count: 3 } }))).toBe(
      '2 frames · 3 annotations',
    );
    expect(
      describeVersion(
        version({ statistics: { frame_count: 1, shape_count: 1, frames_without_annotations: 1 } }),
      ),
    ).toBe('1 frame · 1 annotation · 1 frame with none');
  });

  it('says a draft is not frozen', () => {
    expect(describeVersion(version({ state: 'draft' }))).toMatch(/current annotations.*freeze/);
  });

  it('lists labels most frequent first, and a hash short enough to compare by eye', () => {
    expect(labelCounts(version())).toEqual([
      ['car', 4000],
      ['person', 1300],
    ]);
    expect(shortHash('abcdef0123456789abcdef')).toBe('abcdef012345');
    expect(shortHash(null)).toBe('');
  });

  it('names a download after the project, the version and the format', () => {
    expect(versionFilename('street', version({ name: 'v1.0 final/2' }), 'yolo')).toBe(
      'street-v1.0-final-2-yolo.zip',
    );
  });
});
