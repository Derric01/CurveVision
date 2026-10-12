/**
 * Personal API tokens, from the app.
 *
 * `/auth/tokens` could mint, list and revoke from the start, and the only ways to get a
 * token were `curvevision login` and the API itself — someone wiring a training pipeline or
 * CI job to this server had to know that. These are the rules the panel shows: what state a
 * token is in, which expiries to offer, and the token this very window runs on, which the
 * panel will not offer to revoke.
 */

import type { ApiToken } from '@/api/types';

export type TokenState = 'active' | 'expired' | 'revoked';

export function tokenState(token: ApiToken, now: Date): TokenState {
  if (token.revoked_at) return 'revoked';
  if (token.expires_at && new Date(token.expires_at) <= now) return 'expired';
  return 'active';
}

/** Active tokens first, newest first within each state. */
export function sortTokens(tokens: readonly ApiToken[], now: Date): ApiToken[] {
  const order: Record<TokenState, number> = { active: 0, expired: 1, revoked: 2 };
  return [...tokens].sort(
    (a, b) =>
      order[tokenState(a, now)] - order[tokenState(b, now)] ||
      b.created_at.localeCompare(a.created_at),
  );
}

/** How long a new token lives. A short default: a leaked token should not be forever. */
export const EXPIRY_CHOICES = [
  { id: '30', label: '30 days', days: 30 },
  { id: '90', label: '90 days', days: 90 },
  { id: '365', label: '1 year', days: 365 },
  { id: 'never', label: 'Never', days: null },
] as const;

export type ExpiryChoice = (typeof EXPIRY_CHOICES)[number]['id'];

export function expiresAt(choice: ExpiryChoice, now: Date): string | null {
  const days = EXPIRY_CHOICES.find((option) => option.id === choice)?.days ?? null;
  return days === null ? null : new Date(now.getTime() + days * 86_400_000).toISOString();
}

/**
 * Whether `token` is the one this window authenticates with — the desktop application's
 * launch token. Revoking it would sign the window out of its own server mid-session.
 */
export function isThisWindow(token: ApiToken, windowToken: string | null | undefined): boolean {
  return Boolean(windowToken?.startsWith(`cv_${token.public_id}_`));
}

/** What to paste to use a new token, for the SDK and for the CLI. */
export function usageSnippet(url: string, token: string): string {
  return [
    `export CURVEVISION_URL=${url}`,
    `export CURVEVISION_TOKEN=${token}`,
    'curvevision projects',
  ].join('\n');
}

/** When a token was last used, or that it never has been. */
export function lastUsed(token: ApiToken): string {
  return token.last_used_at
    ? `last used ${new Date(token.last_used_at).toLocaleDateString()}`
    : 'never used';
}
