/**
 * The workspace: who is in it, and (below) the keys scripts use to reach it.
 *
 * CVAT's Organization page is the model — members listed with their role, an owner able to
 * invite, change roles and remove. CVAT invites by email and waits for acceptance; this
 * server has no mail to send, so a person is added directly by the username or email they
 * signed up with, which is what `POST /organizations/{id}/members` already accepted. The
 * rules about who may do what are in `members.ts`, tested without a DOM.
 */

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { UserMinus, UserPlus, Users } from 'lucide-react';

import { api, ApiError } from '@/api/client';
import type { Membership, Organization, Role } from '@/api/types';
import { desktop } from '@/desktop';
import { useSession } from '@/store/session';
import { Badge, Button, EmptyState, ErrorNotice, Input, Panel, Spinner } from '@/ui/primitives';
import { ApiTokensPanel } from './ApiTokensPanel';
import {
  ROLE_DESCRIPTIONS,
  actingRole,
  canAdd,
  canManage,
  canManageMembers,
  describeAddFailure,
  grantableRoles,
  isLastOwner,
  sortMembers,
} from './members';

export function WorkspacePage() {
  const organizations = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.organizations(),
  });
  const [organizationId, setOrganizationId] = useState('');
  const orgs = useMemo(() => organizations.data ?? [], [organizations.data]);
  const organization = orgs.find((org) => org.id === organizationId) ?? orgs[0];

  if (organizations.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-6 py-8" data-workspace-page="">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">
            {organization ? organization.name : 'Workspace'}
          </h1>
          <p className="mt-0.5 max-w-2xl text-sm text-ink-400">
            {desktop
              ? 'Keys for scripts and the SDK to reach this application.'
              : 'Who works here and what each person can do, and keys for scripts and the SDK.'}
          </p>
        </div>
        {orgs.length > 1 && (
          <select
            className="h-9 rounded-md border border-ink-700 bg-ink-950 px-3 text-sm"
            value={organization?.id}
            onChange={(event) => setOrganizationId(event.target.value)}
            aria-label="Workspace"
          >
            {orgs.map((org) => (
              <option key={org.id} value={org.id}>
                {org.name}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* The desktop application has one person in it and no one else who could sign in,
          so a members list there would only ever list them. */}
      {!desktop &&
        (organization ? (
          <MembersPanel organization={organization} />
        ) : (
          <EmptyState
            icon={<Users size={40} />}
            title="No workspace yet"
            description="Create one from the Projects page; then add your team here."
          />
        ))}
      <ApiTokensPanel />
    </div>
  );
}

function MembersPanel({ organization }: { organization: Organization }) {
  const { user } = useSession();
  const actor = actingRole(organization.role, Boolean(user?.is_superuser));
  const members = useQuery({
    queryKey: ['members', organization.id],
    queryFn: () => api.members(organization.id),
  });
  const sorted = useMemo(() => sortMembers(members.data ?? []), [members.data]);

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <Users size={14} /> Members
          {members.data && <span className="text-ink-500">{members.data.length}</span>}
        </span>
      }
    >
      <div className="space-y-4" data-members-panel="">
        {members.isLoading && <Spinner />}
        {members.error && <ErrorNotice error={members.error} />}
        {sorted.length > 0 && (
          <ul className="divide-y divide-ink-800" data-members-list="">
            {sorted.map((member) => (
              <MemberRow
                key={member.id}
                organizationId={organization.id}
                member={member}
                actor={actor}
                isSelf={member.user.id === user?.id}
                lastOwner={isLastOwner(sorted, member)}
              />
            ))}
          </ul>
        )}
        {canManageMembers(actor) ? (
          <AddMember organizationId={organization.id} actor={actor} />
        ) : (
          <p className="text-xs text-ink-500" data-members-ask="">
            Adding people and changing roles needs an admin or owner of {organization.name}.
          </p>
        )}
      </div>
    </Panel>
  );
}

function MemberRow({
  organizationId,
  member,
  actor,
  isSelf,
  lastOwner,
}: {
  organizationId: string;
  member: Membership;
  actor: Role | undefined;
  isSelf: boolean;
  lastOwner: boolean;
}) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['members', organizationId] });
    // Your own role may have changed, which changes what every page offers you.
    void queryClient.invalidateQueries({ queryKey: ['organizations'] });
  };
  const change = useMutation({
    mutationFn: (role: Role) => api.changeMemberRole(organizationId, member.id, role),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api.removeMember(organizationId, member.id),
    onSuccess: refresh,
  });

  const manageable = canManage(actor, member.role) && !lastOwner;
  const roles = grantableRoles(actor);
  const name = member.user.full_name || member.user.username;

  return (
    <li className="py-2.5" data-member={member.user.username}>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm text-ink-100">
            {name}
            {isSelf && <span className="ml-1.5 text-xs text-ink-500">(you)</span>}
          </p>
          {member.user.full_name && (
            <p className="truncate text-xs text-ink-500">{member.user.username}</p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {manageable ? (
            <select
              className="h-7 rounded-md border border-ink-700 bg-ink-950 px-2 text-xs"
              value={member.role}
              disabled={change.isPending}
              onChange={(event) => change.mutate(event.target.value as Role)}
              aria-label={`Role of ${member.user.username}`}
              title={ROLE_DESCRIPTIONS[member.role]}
              data-member-role=""
            >
              {roles.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
          ) : (
            <Badge
              tone={member.role === 'owner' ? 'accent' : 'neutral'}
              className="capitalize"
            >
              <span title={lastOwner ? 'The only owner; promote someone else first.' : undefined}>
                {member.role}
              </span>
            </Badge>
          )}
          {manageable &&
            (confirming ? (
              <>
                <Button
                  size="sm"
                  variant="danger"
                  disabled={remove.isPending}
                  onClick={() => remove.mutate()}
                  data-member-remove-confirm=""
                >
                  {isSelf ? 'Leave' : 'Remove'}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                  Cancel
                </Button>
              </>
            ) : (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setConfirming(true)}
                title={`Remove ${member.user.username}`}
                data-member-remove=""
              >
                <UserMinus size={14} />
              </Button>
            ))}
        </div>
      </div>
      {confirming && (
        <p className="mt-1.5 text-xs text-amber-300">
          {isSelf
            ? 'You will lose access to this workspace and everything in it.'
            : `${name} loses access to this workspace. Jobs assigned to them stay assigned.`}
        </p>
      )}
      {change.error && <ErrorNotice error={change.error} />}
      {remove.error && <ErrorNotice error={remove.error} />}
    </li>
  );
}

function AddMember({ organizationId, actor }: { organizationId: string; actor: Role | undefined }) {
  const queryClient = useQueryClient();
  const roles = grantableRoles(actor);
  const [identifier, setIdentifier] = useState('');
  const [role, setRole] = useState<Role>('annotator');
  const chosen = roles.includes(role) ? role : roles[0]!;
  const [added, setAdded] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: () => api.addMember(organizationId, { identifier: identifier.trim(), role: chosen }),
    onSuccess: (membership) => {
      setAdded(`${membership.user.username} added as ${membership.role}.`);
      setIdentifier('');
      void queryClient.invalidateQueries({ queryKey: ['members', organizationId] });
    },
  });
  const failure =
    add.error instanceof ApiError ? describeAddFailure(add.error.status, identifier) : null;

  return (
    <form
      className="space-y-2 border-t border-ink-800 pt-4"
      onSubmit={(event) => {
        event.preventDefault();
        setAdded(null);
        if (canAdd({ identifier, role: chosen }, actor)) add.mutate();
      }}
      data-member-add=""
    >
      <div className="grid gap-3 sm:grid-cols-[1fr_10rem_auto] sm:items-end">
        <Input
          label="Add someone"
          placeholder="Username or email"
          value={identifier}
          onChange={(event) => setIdentifier(event.target.value)}
          autoComplete="off"
          data-member-identifier=""
        />
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-300">Role</span>
          <select
            className="h-9 w-full rounded-md border border-ink-700 bg-ink-950 px-3 text-sm"
            value={chosen}
            onChange={(event) => setRole(event.target.value as Role)}
            data-member-new-role=""
          >
            {roles.map((candidate) => (
              <option key={candidate} value={candidate}>
                {candidate}
              </option>
            ))}
          </select>
        </label>
        <Button
          type="submit"
          variant="primary"
          disabled={!canAdd({ identifier, role: chosen }, actor) || add.isPending}
          data-member-add-submit=""
        >
          <UserPlus size={14} />
          {add.isPending ? 'Adding…' : 'Add'}
        </Button>
      </div>
      <p className="text-xs text-ink-500">{ROLE_DESCRIPTIONS[chosen]}</p>
      {failure ? (
        <p className="text-sm text-amber-300" data-member-add-failure="">
          {failure}
        </p>
      ) : (
        add.error && <ErrorNotice error={add.error} />
      )}
      {added && (
        <p className="text-sm text-emerald-300" data-member-added="">
          {added}
        </p>
      )}
    </form>
  );
}
