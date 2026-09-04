import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FolderPlus, Layers, Plus } from 'lucide-react';
import { api } from '@/api/client';
import { Badge, Button, EmptyState, ErrorNotice, Input, Spinner } from '@/ui/primitives';

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64);
}

export function ProjectsPage() {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);

  const organizations = useQuery({
    queryKey: ['organizations'],
    queryFn: () => api.organizations(),
  });
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api.projects() });

  const createOrganization = useMutation({
    mutationFn: (name: string) =>
      api.createOrganization({ slug: slugify(name) || 'workspace', name }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['organizations'] }),
  });

  if (organizations.isLoading || projects.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const orgs = organizations.data ?? [];

  if (orgs.length === 0) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-16">
        <EmptyState
          icon={<FolderPlus size={40} />}
          title="Create your first workspace"
          description="A workspace holds your projects, members and models. You will be its owner."
          action={
            <form
              className="mt-2 flex w-full max-w-sm items-end gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                const name = new FormData(event.currentTarget).get('name');
                if (typeof name === 'string' && name.trim()) {
                  createOrganization.mutate(name.trim());
                }
              }}
            >
              <Input name="name" label="Workspace name" placeholder="Acme Vision" required />
              <Button type="submit" variant="primary" disabled={createOrganization.isPending}>
                Create
              </Button>
            </form>
          }
        />
        {createOrganization.error && <ErrorNotice error={createOrganization.error} />}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Projects</h1>
          <p className="mt-0.5 text-sm text-ink-400">
            A project owns a label schema; its tasks own the media.
          </p>
        </div>
        <Button variant="primary" onClick={() => setCreating((open) => !open)}>
          <Plus size={15} />
          New project
        </Button>
      </div>

      {creating && (
        <CreateProjectForm
          organizations={orgs}
          onDone={() => {
            setCreating(false);
            void queryClient.invalidateQueries({ queryKey: ['projects'] });
          }}
        />
      )}

      {projects.data && projects.data.results.length > 0 ? (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {projects.data.results.map((project) => (
            <li key={project.id}>
              <Link
                to={`/projects/${project.id}`}
                className="panel block h-full p-4 transition-colors hover:border-curve-500/50"
              >
                <div className="flex items-start justify-between gap-2">
                  <h2 className="text-sm font-medium text-ink-100">{project.name}</h2>
                  <Badge tone="accent">{project.slug}</Badge>
                </div>
                {project.description && (
                  <p className="mt-2 line-clamp-2 text-xs text-ink-400">{project.description}</p>
                )}
                <p className="mt-3 text-xs text-ink-500">
                  Created {new Date(project.created_at).toLocaleDateString()}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState
          icon={<Layers size={40} />}
          title="No projects yet"
          description="Create a project, define its labels, then add a task with your images."
        />
      )}
    </div>
  );
}

function CreateProjectForm({
  organizations,
  onDone,
}: {
  organizations: { id: string; name: string }[];
  onDone: () => void;
}) {
  const [name, setName] = useState('');
  const [labels, setLabels] = useState('car, pedestrian');
  const [organizationId, setOrganizationId] = useState(organizations[0]?.id ?? '');

  const create = useMutation({
    mutationFn: () =>
      api.createProject({
        organization_id: organizationId,
        slug: slugify(name),
        name,
        labels: labels
          .split(',')
          .map((label) => label.trim())
          .filter(Boolean)
          .map((label) => ({ name: label })),
      }),
    onSuccess: onDone,
  });

  return (
    <form
      className="panel mb-6 space-y-4 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Input
          label="Project name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Street Scenes"
          hint={name ? `Slug: ${slugify(name)}` : undefined}
          required
        />
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-300">Workspace</span>
          <select
            className="h-9 w-full rounded-md border border-ink-700 bg-ink-950 px-3 text-sm"
            value={organizationId}
            onChange={(event) => setOrganizationId(event.target.value)}
          >
            {organizations.map((organization) => (
              <option key={organization.id} value={organization.id}>
                {organization.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <Input
        label="Labels"
        value={labels}
        onChange={(event) => setLabels(event.target.value)}
        hint="Comma separated. You can edit the schema later."
      />

      {create.error && <ErrorNotice error={create.error} />}

      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={create.isPending || !name.trim()}>
          {create.isPending ? 'Creating…' : 'Create project'}
        </Button>
      </div>
    </form>
  );
}
