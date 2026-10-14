/**
 * Webhooks, as the Workspace page offers them: what can be subscribed to, who may manage
 * them, and what a delivery's outcome means to the person whose receiver it was.
 *
 * The API was there from the start and, until iteration 96, never sent anything. The page
 * is shaped after CVAT's webhook settings — events chosen by resource, a ping to check the
 * receiver, the recent deliveries with the receiver's answer.
 */

import type { Role, WebhookDelivery, WebhookEvent } from '@/api/types';
import type { BadgeTone } from '@/ui/primitives';
import { ROLES } from './members';

/** Every event, grouped as a person thinks of them, each with what it means. */
export const EVENT_GROUPS: readonly {
  title: string;
  events: readonly { event: WebhookEvent; label: string }[];
}[] = [
  {
    title: 'Projects',
    events: [
      { event: 'project.created', label: 'created' },
      { event: 'project.updated', label: 'edited' },
    ],
  },
  {
    title: 'Tasks',
    events: [
      { event: 'task.created', label: 'created' },
      { event: 'task.updated', label: 'edited' },
      { event: 'task.completed', label: 'completed — every job accepted' },
    ],
  },
  {
    title: 'Jobs',
    events: [
      { event: 'job.updated', label: 'assigned, locked or moved on' },
      { event: 'job.submitted', label: 'submitted for review' },
      { event: 'job.accepted', label: 'accepted' },
      { event: 'job.rejected', label: 'sent back' },
    ],
  },
  {
    title: 'Work',
    events: [
      { event: 'annotations.updated', label: 'annotations saved' },
      { event: 'issue.created', label: 'issue opened' },
      { event: 'dataset.released', label: 'dataset version released' },
    ],
  },
];

export const ALL_EVENTS: readonly WebhookEvent[] = EVENT_GROUPS.flatMap((group) =>
  group.events.map((entry) => entry.event),
);

/** The events a new webhook starts with: the ones a pipeline usually waits for. */
export const DEFAULT_EVENTS: readonly WebhookEvent[] = ['task.completed', 'dataset.released'];

/** Managing webhooks takes maintainer rank, as the server's policy table says. */
export function canManageWebhooks(actor: Role | undefined): boolean {
  if (actor === undefined) return false;
  return ROLES.indexOf(actor) >= ROLES.indexOf('maintainer');
}

/** An address the server will take: absolute http(s), with a host. */
export function isTargetUrl(value: string): boolean {
  try {
    const url = new URL(value.trim());
    return (url.protocol === 'https:' || url.protocol === 'http:') && url.hostname !== '';
  } catch {
    return false;
  }
}

/** "every event", or the events by name. */
export function describeEvents(events: readonly string[]): string {
  if (ALL_EVENTS.every((event) => events.includes(event))) return 'every event';
  return [...events].sort().join(', ');
}

export interface DeliveryOutcome {
  tone: BadgeTone;
  /** Short: a badge. */
  label: string;
  /** What happened, for the person whose receiver it was. */
  detail: string;
}

/**
 * What a delivery's record means. Who is wrong decides the wording, as it decides the
 * retry (`services/webhooks.py`): a 4xx is the receiver saying no, which retrying will
 * not change; a 5xx or no answer at all is the receiver struggling.
 */
export function deliveryOutcome(delivery: WebhookDelivery): DeliveryOutcome {
  const status = delivery.status_code ?? null;
  const reason = (delivery.error ?? '').trim();
  const attempts = `${delivery.attempts} attempt${delivery.attempts === 1 ? '' : 's'}`;
  if (delivery.delivered_at) {
    return { tone: 'success', label: `${status ?? 'ok'}`, detail: `Delivered (${attempts}).` };
  }
  const answered = status === null ? 'Nothing answered' : `The receiver answered ${status}`;
  const why = reason ? `: ${truncate(reason, 160)}` : '.';
  if (delivery.retrying) {
    return {
      tone: 'warning',
      label: status === null ? 'retrying' : `${status} · retrying`,
      detail: `${answered}${why} It will be tried again (${attempts} so far).`,
    };
  }
  const given =
    status !== null && status >= 400 && status < 500 && status !== 408 && status !== 429
      ? 'Not retried: the receiver refused it, and asking again would not change that.'
      : `Given up after ${attempts}.`;
  return {
    tone: 'danger',
    label: status === null ? 'no answer' : `${status}`,
    detail: `${answered}${why} ${given}`,
  };
}

function truncate(text: string, length: number): string {
  return text.length <= length ? text : `${text.slice(0, length - 1)}…`;
}

/**
 * How to check a delivery is genuine, in the receiver's own language. The page shows it
 * beside the secret, which is shown once.
 */
export function verificationSnippet(secret: string): string {
  return [
    'import hmac, hashlib',
    '',
    `SECRET = b"${secret}"`,
    '',
    'def genuine(body: bytes, signature: str) -> bool:',
    '    expected = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()',
    '    return hmac.compare_digest(signature, expected)  # X-CurveVision-Signature',
  ].join('\n');
}
