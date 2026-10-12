/**
 * Who is in a workspace, and who may change that.
 *
 * The members endpoints existed from the start, and the only way to use them was the API:
 * a team on a shared server could not add a colleague from the app. These are the rules
 * the page shows, mirroring the server's (`services/organizations.py`), so a person is
 * offered only what will succeed rather than a form that answers 403:
 *
 * - managing members at all takes admin rank (the policy engine's `MEMBERSHIP` floor);
 * - below the owner, a member manages only members ranked below them and gives only roles
 *   below their own — CVAT's rule; without it an admin could make themselves owner;
 * - an owner manages anyone, other owners included; the last owner stays an owner.
 */

import type { Membership, Role } from '@/api/types';

/** Least to most privileged, as the server ranks them. */
export const ROLES: readonly Role[] = [
  'viewer',
  'annotator',
  'reviewer',
  'maintainer',
  'admin',
  'owner',
];

/** What each role can do, in a sentence a person choosing one can act on. */
export const ROLE_DESCRIPTIONS: Record<Role, string> = {
  viewer: 'Sees projects and their progress; changes nothing.',
  annotator: 'Annotates the jobs assigned to them.',
  reviewer: 'Annotates, and accepts or rejects others’ jobs.',
  maintainer: 'Creates projects and tasks, imports and exports, assigns work.',
  admin: 'Everything a maintainer does, and manages the members below admin.',
  owner: 'Everything, including other admins and owners.',
};

function rank(role: Role): number {
  return ROLES.indexOf(role);
}

/**
 * The rank someone acts with. A superuser is bound by no workspace rank, as on the server;
 * `undefined` is a server too old to report the caller's role, which then decides itself.
 */
export function actingRole(role: Role | undefined, isSuperuser: boolean): Role | undefined {
  return isSuperuser ? 'owner' : role;
}

/** Whether to offer member management at all. */
export function canManageMembers(actor: Role | undefined): boolean {
  return actor === 'admin' || actor === 'owner';
}

/** The roles `actor` may give, least privileged first. */
export function grantableRoles(actor: Role | undefined): Role[] {
  if (!canManageMembers(actor)) return [];
  if (actor === 'owner') return [...ROLES];
  return ROLES.filter((role) => rank(role) < rank(actor!));
}

/** Whether `actor` may change or remove a member who holds `target`. */
export function canManage(actor: Role | undefined, target: Role): boolean {
  if (!canManageMembers(actor)) return false;
  return actor === 'owner' || rank(target) < rank(actor!);
}

/** Whether this is the workspace's only owner, whom the server will not demote or remove. */
export function isLastOwner(members: readonly Membership[], member: Membership): boolean {
  return member.role === 'owner' && members.filter((m) => m.role === 'owner').length === 1;
}

/** Members, most privileged first, then by name: who runs the place reads first. */
export function sortMembers(members: readonly Membership[]): Membership[] {
  return [...members].sort(
    (a, b) => rank(b.role) - rank(a.role) || a.user.username.localeCompare(b.user.username),
  );
}

/** Whether the add form can be sent: someone named, and a role this person may give. */
export function canAdd(input: { identifier: string; role: Role }, actor: Role | undefined): boolean {
  return input.identifier.trim().length > 0 && grantableRoles(actor).includes(input.role);
}

/** Why the server refused an add, in terms of what to do next. */
export function describeAddFailure(status: number | undefined, identifier: string): string | null {
  if (status === 404) {
    return (
      `No account matches “${identifier.trim()}”. They need to sign up on this server ` +
      'first; then add them by the username or email they used.'
    );
  }
  if (status === 409) return `“${identifier.trim()}” is already a member.`;
  return null;
}
