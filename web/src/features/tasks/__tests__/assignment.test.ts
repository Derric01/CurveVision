/**
 * Who the job picker offers, and what it sends.
 *
 * The `null`-versus-omitted rule is the one with teeth: the server distinguishes them so
 * that a job can be unassigned at all, and a picker that dropped the key instead would
 * silently leave the previous holder in place while looking like it had cleared them.
 */

import { describe, expect, it } from 'vitest';
import type { Membership, Role } from '@/api/types';
import { assignmentCandidates, assignmentPatch, describePerson } from '../assignment';

function member(username: string, role: Role, fullName: string | null = null): Membership {
  return {
    id: `m-${username}`,
    organization_id: 'org-1',
    role,
    user: { id: `u-${username}`, username, full_name: fullName },
  };
}

describe('describePerson', () => {
  it('leads with the full name but always keeps the username, which is the unique one', () => {
    expect(describePerson({ id: 'u', username: 'sam', full_name: 'Sam Okonkwo' })).toBe(
      'Sam Okonkwo (sam)',
    );
  });

  it('falls back to the username rather than rendering an empty option', () => {
    expect(describePerson({ id: 'u', username: 'sam', full_name: null })).toBe('sam');
  });
});

describe('assignmentCandidates', () => {
  it('offers everyone who can hold a job, and not a viewer', () => {
    const people = assignmentCandidates([
      member('ann', 'annotator'),
      member('vic', 'viewer'),
      member('own', 'owner'),
    ]);
    expect(people.map((person) => person.id)).toEqual(['u-ann', 'u-own']);
  });

  it('narrows to the ranks the policy engine lets review', () => {
    const people = assignmentCandidates(
      [member('ann', 'annotator'), member('rev', 'reviewer'), member('adm', 'admin')],
      { forReview: true },
    );
    expect(people.map((person) => person.id)).toEqual(['u-adm', 'u-rev']);
  });

  it('sorts by what is on screen, so the list does not reshuffle as the team grows', () => {
    const people = assignmentCandidates([
      member('zoe', 'annotator'),
      member('amy', 'annotator'),
      member('bob', 'annotator', 'Aaron Bobson'),
    ]);
    expect(people.map((person) => person.label)).toEqual([
      'Aaron Bobson (bob)',
      'amy',
      'zoe',
    ]);
  });

  it('copes with members that have not loaded yet', () => {
    expect(assignmentCandidates(undefined)).toEqual([]);
  });
});

describe('assignmentPatch', () => {
  it('sends the chosen person', () => {
    expect(assignmentPatch('assignee_id', 'u-ann')).toEqual({ assignee_id: 'u-ann' });
  });

  // The server tells an omitted field from an explicit null, and only null unassigns.
  it('turns the empty option into an explicit null rather than dropping the key', () => {
    expect(assignmentPatch('assignee_id', '')).toEqual({ assignee_id: null });
    expect('assignee_id' in assignmentPatch('assignee_id', '')).toBe(true);
  });

  it('works the same for the reviewer', () => {
    expect(assignmentPatch('reviewer_id', '')).toEqual({ reviewer_id: null });
  });
});
