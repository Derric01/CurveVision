import { describe, expect, it } from 'vitest';
import type { Membership, Role } from '@/api/types';
import {
  actingRole,
  canAdd,
  canManage,
  canManageMembers,
  describeAddFailure,
  grantableRoles,
  isLastOwner,
  sortMembers,
} from '../members';

function member(username: string, role: Role): Membership {
  return {
    id: `m-${username}`,
    organization_id: 'org',
    role,
    user: { id: `u-${username}`, username, full_name: null },
  };
}

describe('who may manage members', () => {
  it('is admins and owners', () => {
    expect(canManageMembers('owner')).toBe(true);
    expect(canManageMembers('admin')).toBe(true);
    expect(canManageMembers('maintainer')).toBe(false);
    expect(canManageMembers('annotator')).toBe(false);
    expect(canManageMembers(undefined)).toBe(false);
  });

  it('treats a superuser as an owner, as the server does', () => {
    expect(actingRole('viewer', true)).toBe('owner');
    expect(actingRole('admin', false)).toBe('admin');
  });
});

describe('what an admin may do', () => {
  it('gives only roles below admin', () => {
    expect(grantableRoles('admin')).toEqual(['viewer', 'annotator', 'reviewer', 'maintainer']);
  });

  it('manages only members below admin — not owners, other admins or themselves', () => {
    expect(canManage('admin', 'maintainer')).toBe(true);
    expect(canManage('admin', 'admin')).toBe(false);
    expect(canManage('admin', 'owner')).toBe(false);
  });
});

describe('what an owner may do', () => {
  it('gives any role and manages anyone', () => {
    expect(grantableRoles('owner')).toContain('owner');
    expect(canManage('owner', 'owner')).toBe(true);
  });
});

describe('everyone else', () => {
  it('is offered nothing', () => {
    expect(grantableRoles('maintainer')).toEqual([]);
    expect(canManage('maintainer', 'viewer')).toBe(false);
  });
});

describe('the last owner', () => {
  it('is protected only while there is no other owner', () => {
    const founder = member('founder', 'owner');
    expect(isLastOwner([founder, member('ann', 'annotator')], founder)).toBe(true);
    expect(isLastOwner([founder, member('second', 'owner')], founder)).toBe(false);
  });
});

describe('the list', () => {
  it('reads most privileged first, then by name', () => {
    const sorted = sortMembers([
      member('zed', 'annotator'),
      member('amy', 'annotator'),
      member('boss', 'owner'),
    ]);
    expect(sorted.map((m) => m.user.username)).toEqual(['boss', 'amy', 'zed']);
  });
});

describe('adding someone', () => {
  it('needs a name and a role this person may give', () => {
    expect(canAdd({ identifier: 'ann', role: 'annotator' }, 'admin')).toBe(true);
    expect(canAdd({ identifier: '  ', role: 'annotator' }, 'admin')).toBe(false);
    expect(canAdd({ identifier: 'ann', role: 'owner' }, 'admin')).toBe(false);
  });

  it('says what to do when nobody matches or they are already in', () => {
    expect(describeAddFailure(404, ' ann@example.com ')).toMatch(
      /No account matches “ann@example.com”.*sign up/,
    );
    expect(describeAddFailure(409, 'ann')).toBe('“ann” is already a member.');
    expect(describeAddFailure(403, 'ann')).toBeNull();
  });
});
