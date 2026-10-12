/**
 * Rename and delete, for a project's or a task's page header. The rules are in `manage.ts`.
 */

import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Pencil, Trash2 } from 'lucide-react';

import type { Role } from '@/api/types';
import { Button, ErrorNotice, Input } from '@/ui/primitives';
import {
  MAX_NAME,
  canDelete,
  canRename,
  deleteConfirmed,
  deleteConsequence,
  renamed,
  type Resource,
} from './manage';

export function ManageMenu({
  resource,
  name,
  role,
  counts,
  onRename,
  onDelete,
}: {
  resource: Resource;
  name: string;
  role: Role | undefined;
  counts: { tasks?: number; frames?: number };
  onRename: (name: string) => Promise<unknown>;
  onDelete: () => Promise<unknown>;
}) {
  const [mode, setMode] = useState<'idle' | 'rename' | 'delete'>('idle');
  const [draft, setDraft] = useState(name);
  const [typed, setTyped] = useState('');

  const rename = useMutation({
    mutationFn: (next: string) => onRename(next),
    onSuccess: () => setMode('idle'),
  });
  const remove = useMutation({ mutationFn: () => onDelete() });
  const next = renamed(name, draft, MAX_NAME[resource]);

  if (!canRename(role) && !canDelete(resource, role)) return null;

  if (mode === 'rename') {
    return (
      <form
        className="flex w-full max-w-2xl basis-full items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (next) rename.mutate(next);
        }}
        data-manage-rename=""
      >
        <div className="flex-1">
          <Input
            label={`Rename ${resource}`}
            value={draft}
            maxLength={MAX_NAME[resource]}
            autoFocus
            onChange={(event) => setDraft(event.target.value)}
            data-manage-rename-input=""
          />
        </div>
        <Button
          type="submit"
          variant="primary"
          disabled={!next || rename.isPending}
          data-manage-rename-save=""
        >
          Save
        </Button>
        <Button variant="ghost" onClick={() => setMode('idle')}>
          Cancel
        </Button>
        {rename.error && <ErrorNotice error={rename.error} />}
      </form>
    );
  }

  if (mode === 'delete') {
    return (
      <div
        className="w-full max-w-2xl basis-full space-y-2 rounded-md border border-red-500/30 bg-red-500/10 p-3"
        data-manage-delete=""
      >
        <p className="text-sm text-red-200">{deleteConsequence(resource, counts)}</p>
        <Input
          label={`Type “${name}” to confirm`}
          value={typed}
          autoFocus
          onChange={(event) => setTyped(event.target.value)}
          data-manage-delete-input=""
        />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setMode('idle')}>
            Cancel
          </Button>
          <Button
            variant="danger"
            disabled={!deleteConfirmed(name, typed) || remove.isPending}
            onClick={() => remove.mutate()}
            data-manage-delete-confirm=""
          >
            <Trash2 size={14} />
            {remove.isPending ? 'Deleting…' : `Delete ${resource}`}
          </Button>
        </div>
        {remove.error && <ErrorNotice error={remove.error} />}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1" data-manage="">
      {canRename(role) && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setDraft(name);
            setMode('rename');
          }}
          title={`Rename this ${resource}`}
          data-manage-rename-open=""
        >
          <Pencil size={13} />
        </Button>
      )}
      {canDelete(resource, role) && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setTyped('');
            setMode('delete');
          }}
          title={`Delete this ${resource}`}
          data-manage-delete-open=""
        >
          <Trash2 size={13} />
        </Button>
      )}
    </div>
  );
}
