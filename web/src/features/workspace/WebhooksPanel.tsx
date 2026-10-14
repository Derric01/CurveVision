/**
 * A workspace's webhooks: register a receiver, choose what it hears, check it with a test
 * delivery, and see what it answered. The rules are in `webhooks.ts`.
 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, Pause, Play, Send, Trash2, Webhook as WebhookIcon } from 'lucide-react';

import { api } from '@/api/client';
import type {
  Organization,
  Webhook,
  WebhookCreated,
  WebhookDelivery,
  WebhookEvent,
} from '@/api/types';
import { Badge, Button, ErrorNotice, Input, Panel, Spinner } from '@/ui/primitives';
import {
  DEFAULT_EVENTS,
  EVENT_GROUPS,
  deliveryOutcome,
  describeEvents,
  isTargetUrl,
  verificationSnippet,
} from './webhooks';

export function WebhooksPanel({ organization }: { organization: Organization }) {
  const queryClient = useQueryClient();
  const key = ['webhooks', organization.id];
  const hooks = useQuery({ queryKey: key, queryFn: () => api.webhooks(organization.id) });
  const [created, setCreated] = useState<WebhookCreated | null>(null);

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <WebhookIcon size={14} /> Webhooks
          {hooks.data && <span className="text-ink-500">{hooks.data.length}</span>}
        </span>
      }
    >
      <div className="space-y-4" data-webhooks="">
        <p className="text-xs text-ink-500">
          A webhook tells another system when something happens in {organization.name} — a task
          finished, a dataset released — by sending it a signed POST. Each delivery is signed
          with the webhook&apos;s secret, so the receiver can tell it came from here.
        </p>

        {hooks.isLoading && <Spinner />}
        {hooks.error && <ErrorNotice error={hooks.error} />}
        {hooks.data && hooks.data.length > 0 && (
          <ul className="divide-y divide-ink-800" data-webhook-list="">
            {hooks.data.map((hook) => (
              <WebhookRow
                key={hook.id}
                hook={hook}
                onChanged={() => void queryClient.invalidateQueries({ queryKey: key })}
              />
            ))}
          </ul>
        )}

        {created && <CreatedNotice created={created} onDone={() => setCreated(null)} />}

        <CreateWebhook
          organizationId={organization.id}
          onCreated={(hook) => {
            setCreated(hook);
            void queryClient.invalidateQueries({ queryKey: key });
          }}
        />
      </div>
    </Panel>
  );
}

function WebhookRow({ hook, onChanged }: { hook: Webhook; onChanged: () => void }) {
  const [showDeliveries, setShowDeliveries] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const queryClient = useQueryClient();
  const deliveriesKey = ['webhook-deliveries', hook.id];

  const ping = useMutation({
    mutationFn: () => api.pingWebhook(hook.id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: deliveriesKey }),
  });
  const toggle = useMutation({
    mutationFn: () => api.updateWebhook(hook.id, { is_active: !hook.is_active }),
    onSuccess: onChanged,
  });
  const remove = useMutation({ mutationFn: () => api.deleteWebhook(hook.id), onSuccess: onChanged });
  const pinged = ping.data ? deliveryOutcome(ping.data) : null;

  return (
    <li className="space-y-2 py-3" data-webhook={hook.target_url}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-mono text-sm text-ink-100">{hook.target_url}</p>
          <p className="text-xs text-ink-500">
            {describeEvents(hook.events)}
            {hook.project_id && ' · one project'}
            {hook.description && ` · ${hook.description}`}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {!hook.is_active && (
            <span data-webhook-paused="">
              <Badge tone="warning">paused</Badge>
            </span>
          )}
          <Button
            size="sm"
            variant="ghost"
            disabled={ping.isPending}
            onClick={() => ping.mutate()}
            title="Send a signed test delivery (event “ping”) now"
            data-webhook-ping=""
          >
            <Send size={14} />
            {ping.isPending ? 'Sending…' : 'Send a test'}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={toggle.isPending}
            onClick={() => toggle.mutate()}
            data-webhook-toggle=""
          >
            {hook.is_active ? <Pause size={14} /> : <Play size={14} />}
            {hook.is_active ? 'Pause' : 'Resume'}
          </Button>
          {confirmDelete ? (
            <Button
              size="sm"
              variant="danger"
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
              data-webhook-delete-confirm=""
            >
              Delete it
            </Button>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setConfirmDelete(true)}
              title="Delete this webhook and its delivery history"
              data-webhook-delete=""
            >
              <Trash2 size={14} />
            </Button>
          )}
        </div>
      </div>

      {pinged && (
        <p
          className="flex items-center gap-2 text-xs text-ink-300"
          data-webhook-ping-result={pinged.tone}
        >
          <Badge tone={pinged.tone}>{pinged.label}</Badge>
          {pinged.detail}
        </p>
      )}
      {ping.error && <ErrorNotice error={ping.error} />}
      {toggle.error && <ErrorNotice error={toggle.error} />}
      {remove.error && <ErrorNotice error={remove.error} />}

      <button
        type="button"
        className="text-xs text-ink-400 hover:text-ink-200"
        onClick={() => setShowDeliveries((shown) => !shown)}
        data-webhook-deliveries-toggle=""
      >
        {showDeliveries ? 'Hide recent deliveries' : 'Recent deliveries'}
      </button>
      {showDeliveries && <Deliveries webhookId={hook.id} />}
    </li>
  );
}

function Deliveries({ webhookId }: { webhookId: string }) {
  const deliveries = useQuery({
    queryKey: ['webhook-deliveries', webhookId],
    queryFn: () => api.webhookDeliveries(webhookId),
  });
  if (deliveries.isLoading) return <Spinner />;
  if (deliveries.error) return <ErrorNotice error={deliveries.error} />;
  const rows = deliveries.data ?? [];
  if (rows.length === 0) {
    return <p className="text-xs text-ink-500">Nothing sent yet.</p>;
  }
  return (
    <ul className="space-y-1" data-webhook-deliveries="">
      {rows.map((delivery) => (
        <DeliveryRow key={delivery.id} delivery={delivery} />
      ))}
    </ul>
  );
}

function DeliveryRow({ delivery }: { delivery: WebhookDelivery }) {
  const outcome = deliveryOutcome(delivery);
  return (
    <li
      className="flex items-center gap-2 text-xs text-ink-400"
      title={outcome.detail}
      data-webhook-delivery={delivery.event}
      data-webhook-delivery-tone={outcome.tone}
    >
      <Badge tone={outcome.tone}>{outcome.label}</Badge>
      <span className="font-mono text-ink-300">{delivery.event}</span>
      <span>{new Date(delivery.created_at).toLocaleString()}</span>
    </li>
  );
}

function CreatedNotice({ created, onDone }: { created: WebhookCreated; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <div
      className="space-y-2 rounded-md border border-curve-500/30 bg-curve-500/10 p-3"
      data-webhook-created=""
    >
      <p className="text-sm text-curve-200">
        Give the receiver this signing secret now — it is shown only this once.
      </p>
      <div className="flex items-center gap-2">
        <code
          className="min-w-0 flex-1 truncate rounded bg-ink-950 px-2 py-1.5 font-mono text-xs text-ink-100"
          data-webhook-secret=""
        >
          {created.secret}
        </code>
        <Button
          size="sm"
          onClick={() => {
            void navigator.clipboard
              ?.writeText(created.secret)
              .then(() => setCopied(true))
              .catch(() => setCopied(false));
          }}
        >
          <Copy size={14} />
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
      <pre className="overflow-x-auto rounded bg-ink-950 px-2 py-1.5 font-mono text-xs text-ink-300">
        {verificationSnippet(created.secret)}
      </pre>
      <div className="flex justify-end">
        <Button size="sm" variant="ghost" onClick={onDone}>
          Done
        </Button>
      </div>
    </div>
  );
}

function CreateWebhook({
  organizationId,
  onCreated,
}: {
  organizationId: string;
  onCreated: (hook: WebhookCreated) => void;
}) {
  const [url, setUrl] = useState('');
  const [description, setDescription] = useState('');
  const [events, setEvents] = useState<WebhookEvent[]>([...DEFAULT_EVENTS]);
  const valid = isTargetUrl(url) && events.length > 0;

  const create = useMutation({
    mutationFn: () =>
      api.createWebhook(organizationId, {
        target_url: url.trim(),
        events,
        description: description.trim() || null,
      }),
    onSuccess: (hook) => {
      onCreated(hook);
      setUrl('');
      setDescription('');
      setEvents([...DEFAULT_EVENTS]);
    },
  });

  const flip = (event: WebhookEvent) =>
    setEvents((chosen) =>
      chosen.includes(event) ? chosen.filter((e) => e !== event) : [...chosen, event],
    );

  return (
    <form
      className="space-y-3 border-t border-ink-800 pt-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid) create.mutate();
      }}
      data-webhook-create=""
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Input
          label="New webhook"
          placeholder="https://ci.example.com/curvevision"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          error={url.trim() && !isTargetUrl(url) ? 'An http:// or https:// address' : undefined}
          data-webhook-url=""
        />
        <Input
          label="What it is for"
          placeholder="Optional, e.g. “start training”"
          value={description}
          maxLength={500}
          onChange={(event) => setDescription(event.target.value)}
          data-webhook-description=""
        />
      </div>
      <fieldset className="grid gap-3 sm:grid-cols-4">
        {EVENT_GROUPS.map((group) => (
          <div key={group.title}>
            <legend className="mb-1 text-xs font-medium text-ink-300">{group.title}</legend>
            {group.events.map(({ event, label }) => (
              <label key={event} className="flex items-center gap-2 text-xs text-ink-400">
                <input
                  type="checkbox"
                  checked={events.includes(event)}
                  onChange={() => flip(event)}
                  data-webhook-event={event}
                />
                <span title={event}>{label}</span>
              </label>
            ))}
          </div>
        ))}
      </fieldset>
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-ink-500">
          {events.length === 0 ? 'Choose at least one event.' : describeEvents(events)}
        </p>
        <Button
          type="submit"
          variant="primary"
          disabled={!valid || create.isPending}
          data-webhook-submit=""
        >
          {create.isPending ? 'Adding…' : 'Add webhook'}
        </Button>
      </div>
      {create.error && <ErrorNotice error={create.error} />}
    </form>
  );
}
