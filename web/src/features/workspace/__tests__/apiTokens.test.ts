import { describe, expect, it } from 'vitest';
import type { ApiToken } from '@/api/types';
import {
  expiresAt,
  isThisWindow,
  lastUsed,
  sortTokens,
  tokenState,
  usageSnippet,
} from '../apiTokens';

const NOW = new Date('2026-10-12T12:00:00Z');

function token(overrides: Partial<ApiToken> = {}): ApiToken {
  return {
    id: 't',
    name: 'ci',
    public_id: 'abcd1234abcd1234',
    created_at: '2026-10-01T00:00:00Z',
    expires_at: null,
    last_used_at: null,
    revoked_at: null,
    ...overrides,
  };
}

describe('a token’s state', () => {
  it('is revoked, expired or active, in that order of precedence', () => {
    expect(tokenState(token(), NOW)).toBe('active');
    expect(tokenState(token({ expires_at: '2026-10-11T00:00:00Z' }), NOW)).toBe('expired');
    expect(
      tokenState(
        token({ expires_at: '2026-10-11T00:00:00Z', revoked_at: '2026-10-05T00:00:00Z' }),
        NOW,
      ),
    ).toBe('revoked');
  });

  it('lists active tokens first, newest first', () => {
    const sorted = sortTokens(
      [
        token({ id: 'old', created_at: '2026-01-01T00:00:00Z' }),
        token({ id: 'gone', revoked_at: '2026-10-02T00:00:00Z' }),
        token({ id: 'new', created_at: '2026-10-10T00:00:00Z' }),
      ],
      NOW,
    );
    expect(sorted.map((t) => t.id)).toEqual(['new', 'old', 'gone']);
  });
});

describe('expiry', () => {
  it('is a date that many days out, or none', () => {
    expect(expiresAt('30', NOW)).toBe('2026-11-11T12:00:00.000Z');
    expect(expiresAt('never', NOW)).toBeNull();
  });
});

describe('the token this window runs on', () => {
  it('is recognised by its public id, and only by the whole segment', () => {
    expect(isThisWindow(token(), 'cv_abcd1234abcd1234_secret')).toBe(true);
    expect(isThisWindow(token(), 'cv_abcd1234abcd12345_secret')).toBe(false);
    expect(isThisWindow(token(), null)).toBe(false);
  });
});

describe('using a new token', () => {
  it('is the two environment variables the SDK and CLI read', () => {
    expect(usageSnippet('https://cv.example.com', 'cv_x_y')).toBe(
      'export CURVEVISION_URL=https://cv.example.com\n' +
        'export CURVEVISION_TOKEN=cv_x_y\ncurvevision projects',
    );
  });

  it('says when it was last used, or that it never was', () => {
    expect(lastUsed(token())).toBe('never used');
    expect(lastUsed(token({ last_used_at: '2026-10-10T00:00:00Z' }))).toMatch(/^last used /);
  });
});
