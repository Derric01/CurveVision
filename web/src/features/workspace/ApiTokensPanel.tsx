/**
 * Mint, see and revoke personal API tokens — for the SDK, the CLI, CI and training jobs.
 * The rules are in `apiTokens.ts`.
 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, KeyRound, Trash2 } from 'lucide-react';

import { api } from '@/api/client';
import type { ApiTokenCreated } from '@/api/types';
import { desktop } from '@/desktop';
import { Badge, Button, ErrorNotice, Input, Panel, Spinner } from '@/ui/primitives';
import {
  EXPIRY_CHOICES,
  expiresAt,
  isThisWindow,
  lastUsed,
  sortTokens,
  tokenState,
  usageSnippet,
  type ExpiryChoice,
} from './apiTokens';

export function ApiTokensPanel() {
  const queryClient = useQueryClient();
  const tokens = useQuery({ queryKey: ['api-tokens'], queryFn: () => api.apiTokens() });
  const now = new Date();
  const sorted = sortTokens(tokens.data ?? [], now);
  const [showRevoked, setShowRevoked] = useState(false);
  const shown = showRevoked ? sorted : sorted.filter((t) => tokenState(t, now) !== 'revoked');
  const hidden = sorted.length - shown.length;

  const [name, setName] = useState('');
  const [expiry, setExpiry] = useState<ExpiryChoice>('90');
  const [minted, setMinted] = useState<ApiTokenCreated | null>(null);
  const [copied, setCopied] = useState(false);

  const create = useMutation({
    mutationFn: () =>
      api.createApiToken({ name: name.trim(), expires_at: expiresAt(expiry, new Date()) }),
    onSuccess: (created) => {
      setMinted(created);
      setCopied(false);
      setName('');
      void queryClient.invalidateQueries({ queryKey: ['api-tokens'] });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api.revokeApiToken(id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['api-tokens'] }),
  });

  const serverUrl = desktop?.url || window.location.origin;

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <KeyRound size={14} /> API tokens
        </span>
      }
    >
      <div className="space-y-4" data-api-tokens="">
        <p className="text-xs text-ink-500">
          A token lets a script act as you: the Python SDK, the <code>curvevision</code> command,
          a CI job or a training pipeline pulling exports. It can do whatever you can, so give
          each use its own token and revoke the ones you stop using.
          {desktop && ' It works while this application is running.'}
        </p>

        {tokens.isLoading && <Spinner />}
        {tokens.error && <ErrorNotice error={tokens.error} />}
        {shown.length > 0 && (
          <ul className="divide-y divide-ink-800" data-api-token-list="">
            {shown.map((token) => {
              const state = tokenState(token, now);
              const mine = isThisWindow(token, desktop?.token);
              return (
                <li
                  key={token.id}
                  className="flex items-center justify-between gap-3 py-2"
                  data-api-token={token.name}
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm text-ink-100">
                      {token.name}
                      <span className="ml-2 font-mono text-xs text-ink-500">
                        cv_{token.public_id}_…
                      </span>
                    </p>
                    <p className="text-xs text-ink-500">
                      created {new Date(token.created_at).toLocaleDateString()} · {lastUsed(token)}
                      {token.expires_at &&
                        state === 'active' &&
                        ` · expires ${new Date(token.expires_at).toLocaleDateString()}`}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {state !== 'active' && (
                      <Badge tone={state === 'revoked' ? 'danger' : 'warning'}>{state}</Badge>
                    )}
                    {mine && <Badge tone="info">this window</Badge>}
                    {state === 'active' && !mine && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={revoke.isPending}
                        onClick={() => revoke.mutate(token.id)}
                        title={`Revoke ${token.name}: anything using it stops working`}
                        data-api-token-revoke=""
                      >
                        <Trash2 size={14} />
                        Revoke
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {hidden > 0 && (
          <button
            type="button"
            className="text-xs text-ink-400 hover:text-ink-200"
            onClick={() => setShowRevoked(true)}
          >
            Show {hidden} revoked
          </button>
        )}
        {revoke.error && <ErrorNotice error={revoke.error} />}

        {minted && (
          <div
            className="space-y-2 rounded-md border border-curve-500/30 bg-curve-500/10 p-3"
            data-api-token-minted=""
          >
            <p className="text-sm text-curve-200">
              Copy “{minted.name}” now — it is shown only this once. Only a hash of it is kept.
            </p>
            <div className="flex items-center gap-2">
              <code
                className="min-w-0 flex-1 truncate rounded bg-ink-950 px-2 py-1.5 font-mono text-xs text-ink-100"
                data-api-token-secret=""
              >
                {minted.token}
              </code>
              <Button
                size="sm"
                onClick={() => {
                  void navigator.clipboard
                    ?.writeText(minted.token)
                    .then(() => setCopied(true))
                    .catch(() => setCopied(false));
                }}
              >
                <Copy size={14} />
                {copied ? 'Copied' : 'Copy'}
              </Button>
            </div>
            <pre className="overflow-x-auto rounded bg-ink-950 px-2 py-1.5 font-mono text-xs text-ink-300">
              {usageSnippet(serverUrl, minted.token)}
            </pre>
            <div className="flex justify-end">
              <Button size="sm" variant="ghost" onClick={() => setMinted(null)}>
                Done
              </Button>
            </div>
          </div>
        )}

        <form
          className="grid gap-3 border-t border-ink-800 pt-4 sm:grid-cols-[1fr_9rem_auto] sm:items-end"
          onSubmit={(event) => {
            event.preventDefault();
            if (name.trim()) create.mutate();
          }}
          data-api-token-create=""
        >
          <Input
            label="New token"
            placeholder="What it is for, e.g. “training pipeline”"
            value={name}
            maxLength={120}
            onChange={(event) => setName(event.target.value)}
            data-api-token-name=""
          />
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-ink-300">Expires</span>
            <select
              className="h-9 w-full rounded-md border border-ink-700 bg-ink-950 px-3 text-sm"
              value={expiry}
              onChange={(event) => setExpiry(event.target.value as ExpiryChoice)}
              data-api-token-expiry=""
            >
              {EXPIRY_CHOICES.map((choice) => (
                <option key={choice.id} value={choice.id}>
                  {choice.label}
                </option>
              ))}
            </select>
          </label>
          <Button
            type="submit"
            variant="primary"
            disabled={!name.trim() || create.isPending}
            data-api-token-submit=""
          >
            {create.isPending ? 'Creating…' : 'Create token'}
          </Button>
        </form>
        {create.error && <ErrorNotice error={create.error} />}
      </div>
    </Panel>
  );
}
