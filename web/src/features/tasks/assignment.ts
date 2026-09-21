/**
 * Who a job can be handed to, and how to name them.
 *
 * `Job.assignee_id` and `Job.reviewer_id` have been enforced by the policy engine since the
 * first iterations and settable through `PATCH /jobs/{id}` — and no screen set either, so
 * dividing a task between three annotators meant three API calls. The web client did not
 * even have a `members` method to ask who the candidates were.
 *
 * Pure, because the parts worth pinning are the ordering and the naming: a picker that
 * lists people in whatever order the server returned, or that shows a blank option for
 * somebody with no full name, is the kind of thing nobody notices until a real team opens
 * it.
 */

import type { Membership, Role, UserBrief } from '@/api/types';

/** A person the job can be handed to, ready to render as an option. */
export interface Candidate {
  id: string;
  /** What the option reads as. Never empty. */
  label: string;
  role: Role;
}

/** Ranks that can hold a job at all. A viewer cannot annotate, so offering one is a trap. */
const CAN_BE_ASSIGNED: ReadonlySet<Role> = new Set<Role>([
  'annotator',
  'reviewer',
  'maintainer',
  'admin',
  'owner',
]);

/** Ranks the policy engine will let review. Floors at reviewer; see `ROLE_FLOOR`. */
const CAN_REVIEW: ReadonlySet<Role> = new Set<Role>([
  'reviewer',
  'maintainer',
  'admin',
  'owner',
]);

/**
 * How one person reads in the list.
 *
 * The username is always shown, because it is the only identifier that is guaranteed to
 * exist and to be unique — two people called "Sam Okonkwo" in the same organization is not
 * a hypothetical. A full name, where there is one, goes in front of it.
 */
export function describePerson(user: UserBrief): string {
  return user.full_name ? `${user.full_name} (${user.username})` : user.username;
}

/**
 * The people who may hold a job, in a stable order.
 *
 * Sorted by label rather than left in the server's order, which is by join date: a picker
 * that reorders itself as the team grows makes muscle memory useless. `forReview` narrows
 * to the ranks that can actually accept or send work back — offering somebody the job of
 * reviewing when the policy engine will refuse them is offering a 403.
 */
export function assignmentCandidates(
  members: readonly Membership[] | undefined,
  { forReview = false }: { forReview?: boolean } = {},
): Candidate[] {
  const allowed = forReview ? CAN_REVIEW : CAN_BE_ASSIGNED;
  return (members ?? [])
    .filter((member) => allowed.has(member.role))
    .map((member) => ({
      id: member.user.id,
      label: describePerson(member.user),
      role: member.role,
    }))
    .sort((left, right) => left.label.localeCompare(right.label));
}

/**
 * The body for a `PATCH /jobs/{id}` that changes one assignment.
 *
 * The empty string is what an unselected `<option>` carries, and it has to become an
 * explicit `null` rather than being dropped: the server tells "omitted" and "null" apart,
 * and only the second one unassigns.
 */
export function assignmentPatch(
  field: 'assignee_id' | 'reviewer_id',
  selected: string,
): Record<string, string | null> {
  return { [field]: selected === '' ? null : selected };
}
