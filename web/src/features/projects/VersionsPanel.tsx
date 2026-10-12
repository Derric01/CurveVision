/**
 * A project's dataset versions: create one, release it to freeze its annotations, and
 * download any version in any export format. The rules are in `versions.ts`.
 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, GitCommitVertical, Lock, Trash2 } from 'lucide-react';

import { api } from '@/api/client';
import type { DatasetFormat, DatasetVersion, Role } from '@/api/types';
import { Badge, Button, ErrorNotice, Input, Panel, Spinner } from '@/ui/primitives';
import {
  canDeleteDraft,
  canRelease,
  describeVersion,
  labelCounts,
  shortHash,
  versionFilename,
  versionNameProblem,
} from './versions';

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function VersionsPanel({
  projectId,
  projectSlug,
  role,
  formats,
}: {
  projectId: string;
  projectSlug: string;
  role: Role | undefined;
  formats: readonly DatasetFormat[];
}) {
  const queryClient = useQueryClient();
  const versions = useQuery({
    queryKey: ['versions', projectId],
    queryFn: () => api.versions(projectId),
  });
  const [name, setName] = useState('');
  const problem = name ? versionNameProblem(name, versions.data ?? []) : null;
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['versions', projectId] });

  const create = useMutation({
    mutationFn: () => api.createVersion(projectId, { name: name.trim() }),
    onSuccess: () => {
      setName('');
      refresh();
    },
  });

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <GitCommitVertical size={14} /> Dataset versions
        </span>
      }
    >
      <div className="space-y-4" data-versions-panel="">
        <p className="text-xs text-ink-500">
          Release a version to freeze its annotations: downloading it later returns exactly what
          was released, however the project changes after — the dataset an experiment can be
          reproduced from. Its content hash identifies it.
        </p>
        {versions.isLoading && <Spinner />}
        {versions.error && <ErrorNotice error={versions.error} />}
        {(versions.data ?? []).length > 0 && (
          <ul className="divide-y divide-ink-800" data-versions-list="">
            {versions.data!.map((version) => (
              <VersionRow
                key={version.id}
                projectId={projectId}
                projectSlug={projectSlug}
                version={version}
                role={role}
                formats={formats}
                onChanged={refresh}
              />
            ))}
          </ul>
        )}
        {canRelease(role) && (
          <form
            className="flex items-end gap-2 border-t border-ink-800 pt-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (!versionNameProblem(name, versions.data ?? [])) create.mutate();
            }}
            data-version-create=""
          >
            <div className="flex-1">
              <Input
                label="New version"
                placeholder="v1.0"
                value={name}
                maxLength={120}
                onChange={(event) => setName(event.target.value)}
                error={problem ?? undefined}
                data-version-name=""
              />
            </div>
            <Button
              type="submit"
              disabled={!name.trim() || Boolean(problem) || create.isPending}
              data-version-create-submit=""
            >
              Create draft
            </Button>
          </form>
        )}
        {create.error && <ErrorNotice error={create.error} />}
      </div>
    </Panel>
  );
}

function VersionRow({
  projectId,
  projectSlug,
  version,
  role,
  formats,
  onChanged,
}: {
  projectId: string;
  projectSlug: string;
  version: DatasetVersion;
  role: Role | undefined;
  formats: readonly DatasetFormat[];
  onChanged: () => void;
}) {
  const exportable = formats.filter((format) => format.supports_export);
  const [format, setFormat] = useState(exportable[0]?.id ?? 'coco');
  const [withImages, setWithImages] = useState(false);
  const [confirmRelease, setConfirmRelease] = useState(false);

  const release = useMutation({
    mutationFn: () => api.releaseVersion(projectId, version.id),
    onSuccess: () => {
      setConfirmRelease(false);
      onChanged();
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteVersion(projectId, version.id),
    onSuccess: onChanged,
  });
  const exported = useMutation({
    mutationFn: async () => {
      const blob = await api.exportVersion(projectId, version.id, {
        format,
        include_images: withImages,
      });
      download(blob, versionFilename(projectSlug, version, format));
    },
  });
  const released = version.state === 'released';
  const labels = labelCounts(version);

  return (
    <li className="space-y-2 py-3" data-version={version.name}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm text-ink-100">
            {version.name}
            {released ? (
              <Badge tone="success">
                <Lock size={10} className="mr-1 inline" />
                released
              </Badge>
            ) : (
              <Badge>draft</Badge>
            )}
            {released && version.content_hash && (
              <code
                className="font-mono text-xs text-ink-500"
                title={`Content hash ${version.content_hash}`}
                data-version-hash=""
              >
                {shortHash(version.content_hash)}
              </code>
            )}
          </p>
          <p className="text-xs text-ink-500" data-version-summary="">
            {describeVersion(version)}
          </p>
          {labels.length > 0 && (
            <p className="text-xs text-ink-500">
              {labels.map(([label, count]) => `${label} ${count.toLocaleString()}`).join(' · ')}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="h-7 rounded-md border border-ink-700 bg-ink-950 px-2 text-xs"
            value={format}
            onChange={(event) => setFormat(event.target.value)}
            aria-label={`Format for ${version.name}`}
            data-version-format=""
          >
            {exportable.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.name}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-1 text-xs text-ink-400">
            <input
              type="checkbox"
              checked={withImages}
              onChange={(event) => setWithImages(event.target.checked)}
            />
            images
          </label>
          <Button
            size="sm"
            disabled={exported.isPending}
            onClick={() => exported.mutate()}
            data-version-download=""
          >
            <Download size={13} />
            {exported.isPending ? 'Preparing…' : 'Download'}
          </Button>
          {!released && canRelease(role) && (
            <Button
              size="sm"
              variant="primary"
              onClick={() => setConfirmRelease(true)}
              data-version-release=""
            >
              Release
            </Button>
          )}
          {!released && canDeleteDraft(role) && (
            <Button
              size="sm"
              variant="ghost"
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
              title="Delete this draft"
              data-version-delete=""
            >
              <Trash2 size={13} />
            </Button>
          )}
        </div>
      </div>
      {confirmRelease && (
        <div
          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200"
          data-version-release-warning=""
        >
          Releasing freezes the project’s annotations as they are now. The release cannot be
          changed or deleted afterwards — only superseded by a new one.
          <span className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={() => setConfirmRelease(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={release.isPending}
              onClick={() => release.mutate()}
              data-version-release-confirm=""
            >
              {release.isPending ? 'Releasing…' : 'Release'}
            </Button>
          </span>
        </div>
      )}
      {release.error && <ErrorNotice error={release.error} />}
      {remove.error && <ErrorNotice error={remove.error} />}
      {exported.error && <ErrorNotice error={exported.error} />}
    </li>
  );
}
