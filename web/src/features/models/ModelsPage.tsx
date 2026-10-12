/**
 * The models a workspace's annotators can run from the editor's Auto-annotate panel, and
 * the form that adds one from an endpoint and a key.
 *
 * CVAT's Models page is the model for the list — a card per model with its kind and its
 * labels. CVAT's open-source build has no form to add one (its models are Nuclio functions
 * deployed beside it); the form is this project's, because here a model is any server that
 * speaks the inference contract, and pointing at one should take a URL, not a JSON body.
 *
 * The rules — what is sent, what blocks registering — are in `registration.ts`, tested
 * without a DOM.
 */

import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plug, Sparkles, Trash2 } from 'lucide-react';
import { api } from '@/api/client';
import type { DiscoveredModel, ModelRegistration, Organization } from '@/api/types';
import { Badge, Button, EmptyState, ErrorNotice, Input, Spinner } from '@/ui/primitives';
import {
  MODEL_KINDS,
  canRegisterModels,
  connectionOf,
  describeDiscovery,
  emptyDraft,
  fillFrom,
  problemsWith,
  registrationOf,
  type RegistrationDraft,
} from './registration';

export function ModelsPage() {
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
  if (!organization) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-16">
        <EmptyState
          icon={<Sparkles size={40} />}
          title="No workspace yet"
          description="Models belong to a workspace. Create one from the Projects page first."
          action={
            <Link to="/projects" className="text-sm text-curve-300 hover:underline">
              Go to Projects
            </Link>
          }
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-8" data-models-page="">
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Models</h1>
          <p className="mt-0.5 max-w-2xl text-sm text-ink-400">
            What annotators can run from the editor&apos;s Auto-annotate panel. CurveVision
            does not ship a model: add any server that speaks its inference contract — your
            own, Triton, TorchServe, a hosted service behind a small adapter.
          </p>
        </div>
        {orgs.length > 1 && (
          <select
            className="h-9 rounded-md border border-ink-700 bg-ink-950 px-3 text-sm"
            value={organization.id}
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

      <RegisteredModels organization={organization} />
      {canRegisterModels(organization.role) ? (
        <AddModelForm organizationId={organization.id} />
      ) : (
        <p className="mt-6 text-sm text-ink-500" data-models-ask="">
          Adding a model needs a maintainer of {organization.name}. Ask one to add it here.
        </p>
      )}
    </div>
  );
}

function RegisteredModels({ organization }: { organization: Organization }) {
  const models = useQuery({
    queryKey: ['models', organization.id],
    queryFn: () => api.models({ organization_id: organization.id }),
  });

  if (models.isLoading) return <Spinner className="my-6 h-5 w-5" />;
  if (models.error) return <ErrorNotice error={models.error} />;
  const registered = models.data ?? [];
  if (registered.length === 0) {
    return (
      <p className="panel px-4 py-6 text-sm text-ink-400" data-models-empty="">
        No model is registered in {organization.name} yet.
      </p>
    );
  }
  return (
    <ul className="grid gap-3 sm:grid-cols-2">
      {registered.map((model) => (
        <ModelCard
          key={model.id}
          model={model}
          // An instance-wide model is the server operator's, not this workspace's to remove.
          removable={model.organization_id !== null && canRegisterModels(organization.role)}
        />
      ))}
    </ul>
  );
}

function ModelCard({ model, removable }: { model: ModelRegistration; removable: boolean }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const remove = useMutation({
    mutationFn: () => api.deleteModel(model.id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['models'] }),
  });

  return (
    <li className="panel p-4" data-model-card={model.slug}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-medium text-ink-100">{model.name}</h2>
          <p className="mt-0.5 text-xs text-ink-500">
            {model.kind}
            {model.organization_id === null ? ' · shared by this server' : ''}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          {model.open_vocabulary && <Badge tone="accent">open vocabulary</Badge>}
          {removable &&
            (confirming ? (
              <>
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => remove.mutate()}
                  disabled={remove.isPending}
                  data-model-remove-confirm=""
                >
                  Remove
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                  Keep
                </Button>
              </>
            ) : (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setConfirming(true)}
                title="Remove this model"
                data-model-remove=""
              >
                <Trash2 size={14} />
              </Button>
            ))}
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-1" data-model-labels="">
        {model.output_labels.length > 0 ? (
          model.output_labels.map((label) => <Badge key={label}>{label}</Badge>)
        ) : (
          <span className="text-xs text-ink-500">Finds whatever it is asked for.</span>
        )}
      </div>
      {remove.error && (
        <div className="mt-3">
          <ErrorNotice error={remove.error} />
        </div>
      )}
    </li>
  );
}

function AddModelForm({ organizationId }: { organizationId: string }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<RegistrationDraft>(emptyDraft);
  /** The connection discovery last succeeded for, and what it found there. */
  const [connected, setConnected] = useState<{
    endpoint: string;
    apiKey: string;
    found: DiscoveredModel[];
  } | null>(null);
  const [registered, setRegistered] = useState<string | null>(null);

  // A different workspace is a different form.
  useEffect(() => {
    setDraft(emptyDraft());
    setConnected(null);
    setRegistered(null);
  }, [organizationId]);

  const update = (changes: Partial<RegistrationDraft>) =>
    setDraft((current) => ({ ...current, ...changes }));

  const discover = useMutation({
    mutationFn: (current: RegistrationDraft) =>
      api.discoverModels(organizationId, connectionOf(current)),
    onSuccess: ({ models }, current) => {
      setConnected({ endpoint: current.endpoint, apiKey: current.apiKey, found: models });
      setDraft(models.length === 1 ? fillFrom(current, models[0]!) : current);
    },
  });

  const register = useMutation({
    mutationFn: () => api.registerModel(organizationId, registrationOf(draft)),
    onSuccess: (model) => {
      setRegistered(model.name);
      setDraft(emptyDraft());
      setConnected(null);
      void queryClient.invalidateQueries({ queryKey: ['models'] });
    },
  });

  // Connected to *this* endpoint with *this* key: an edit to either needs asking again,
  // or a registration could save a key nobody has seen work.
  const isConnected =
    connected !== null &&
    connected.endpoint === draft.endpoint &&
    connected.apiKey === draft.apiKey;
  const problems = problemsWith(draft);

  return (
    <section className="panel mt-6 space-y-4 p-4" data-add-model="">
      <div>
        <h2 className="text-sm font-medium text-ink-200">Add a model</h2>
        <p className="mt-0.5 text-xs text-ink-500">
          The URL CurveVision sends frames to, and the API key it should send, if the server
          wants one. Connecting asks the server what it serves.
        </p>
      </div>

      <form
        className="grid gap-4 sm:grid-cols-[1fr_16rem_auto] sm:items-end"
        onSubmit={(event) => {
          event.preventDefault();
          setRegistered(null);
          discover.mutate(draft);
        }}
      >
        <Input
          label="Endpoint"
          value={draft.endpoint}
          onChange={(event) => update({ endpoint: event.target.value })}
          placeholder="http://gpu-box:8000/infer"
          data-model-endpoint=""
          required
        />
        <Input
          label="API key"
          type="password"
          autoComplete="off"
          value={draft.apiKey}
          onChange={(event) => update({ apiKey: event.target.value })}
          placeholder="Optional"
          data-model-key=""
        />
        <Button
          type="submit"
          variant="secondary"
          disabled={discover.isPending || !draft.endpoint.trim()}
          data-model-connect=""
        >
          <Plug size={14} className="mr-1.5" />
          {discover.isPending ? 'Connecting…' : 'Connect'}
        </Button>
      </form>

      {discover.error && !discover.isPending && <ErrorNotice error={discover.error} />}
      {registered && (
        <p className="text-sm text-emerald-300" data-model-registered="">
          Added {registered}. Annotators can run it from the editor&apos;s Auto-annotate panel.
        </p>
      )}

      {isConnected && (
        <div className="space-y-4 border-t border-ink-800 pt-4" data-model-details="">
          <p className="text-sm text-ink-300" data-model-discovery="">
            {describeDiscovery(connected.found)}
          </p>

          {connected.found.length > 1 && (
            <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Model">
              {connected.found.map((found) => (
                <Button
                  key={found.id}
                  size="sm"
                  variant="secondary"
                  active={draft.model === found.id}
                  role="radio"
                  aria-checked={draft.model === found.id}
                  onClick={() => setDraft((current) => fillFrom(current, found))}
                  data-model-found={found.id}
                >
                  {found.name}
                </Button>
              ))}
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <Input
              label="Name"
              value={draft.name}
              onChange={(event) => update({ name: event.target.value })}
              hint="What annotators pick it by."
              data-model-name=""
            />
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-ink-300">Kind</span>
              <select
                className="h-9 w-full rounded-md border border-ink-700 bg-ink-950 px-3 text-sm"
                value={draft.kind}
                onChange={(event) =>
                  update({ kind: event.target.value as RegistrationDraft['kind'] })
                }
                data-model-kind=""
              >
                {MODEL_KINDS.map(({ kind, label }) => (
                  <option key={kind} value={kind}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <Input
            label={draft.openVocabulary ? 'Default classes' : 'Labels it returns'}
            value={draft.labels}
            onChange={(event) => update({ labels: event.target.value })}
            placeholder="car, person, bicycle"
            hint={
              draft.openVocabulary
                ? 'Optional. Annotators type what to look for; left empty, it is asked for the project’s labels.'
                : 'Comma separated. In the editor each is matched to a project label of the same name.'
            }
            data-model-labels-input=""
          />

          <label className="flex items-center gap-2 text-sm text-ink-300">
            <input
              type="checkbox"
              checked={draft.openVocabulary}
              onChange={(event) => update({ openVocabulary: event.target.checked })}
              data-model-open-vocabulary=""
            />
            Open vocabulary — it is told what to find, by name (YOLO-World, Grounding DINO)
          </label>

          {problems.length > 0 && (
            <ul className="space-y-1 text-xs text-amber-300" data-model-problems="">
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          )}
          {register.error && <ErrorNotice error={register.error} />}

          <div className="flex justify-end">
            <Button
              variant="primary"
              disabled={problems.length > 0 || register.isPending}
              onClick={() => register.mutate()}
              data-model-register=""
            >
              {register.isPending ? 'Adding…' : 'Add model'}
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
