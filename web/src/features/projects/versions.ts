/**
 * Dataset versions, from the project page.
 *
 * The API could create, release, list and delete versions from the start, and the app showed
 * none of it — a release, the thing an experiment is reproduced from, was API-only. From
 * iteration 66 a release freezes its annotations, and exporting it returns them as they
 * were released. These are the rules the panel shows. Who may act mirrors the policy
 * engine: releasing takes reviewer, deleting a draft maintainer.
 */

import type { DatasetVersion, Role } from '@/api/types';

const RANK: readonly Role[] = ['viewer', 'annotator', 'reviewer', 'maintainer', 'admin', 'owner'];

function atLeast(role: Role | undefined, floor: Role): boolean {
  return role === undefined || RANK.indexOf(role) >= RANK.indexOf(floor);
}

export function canRelease(role: Role | undefined): boolean {
  return atLeast(role, 'reviewer');
}

export function canDeleteDraft(role: Role | undefined): boolean {
  return atLeast(role, 'maintainer');
}

/** Why a new version's name will be refused, or null. */
export function versionNameProblem(name: string, existing: readonly DatasetVersion[]): string | null {
  const trimmed = name.trim();
  if (!trimmed) return 'Name the version, e.g. “v1.0”.';
  if (trimmed.length > 120) return 'At most 120 characters.';
  if (existing.some((version) => version.name === trimmed)) {
    return `There is already a version named “${trimmed}”.`;
  }
  return null;
}

/** A released version in one line: what is in it, and the warning that matters most. */
export function describeVersion(version: DatasetVersion): string {
  if (version.state === 'draft') {
    return 'Draft — exports the project’s current annotations. Release it to freeze them.';
  }
  const stats = version.statistics;
  const parts = [count(stats.frame_count ?? 0, 'frame'), count(stats.shape_count ?? 0, 'annotation')];
  const empty = stats.frames_without_annotations ?? 0;
  if (empty > 0) parts.push(`${count(empty, 'frame')} with none`);
  return parts.join(' · ');
}

function count(n: number, one: string): string {
  return `${n.toLocaleString()} ${one}${n === 1 ? '' : 's'}`;
}

/** The labels a release holds, most frequent first. */
export function labelCounts(version: DatasetVersion): Array<[string, number]> {
  return Object.entries(version.statistics.label_distribution ?? {}).sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
  );
}

/** Short enough to read, long enough to compare two releases by eye. */
export function shortHash(hash: string | null): string {
  return hash ? hash.slice(0, 12) : '';
}

export function versionFilename(projectSlug: string, version: DatasetVersion, format: string) {
  const safe = version.name.replace(/[^A-Za-z0-9._-]+/g, '-');
  return `${projectSlug}-${safe}-${format}.zip`;
}
