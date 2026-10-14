import { describe, expect, it } from 'vitest';
import type { WebhookDelivery } from '@/api/types';
import {
  ALL_EVENTS,
  canManageWebhooks,
  deliveryOutcome,
  describeEvents,
  isTargetUrl,
  verificationSnippet,
} from '../webhooks';

function delivery(overrides: Partial<WebhookDelivery> = {}): WebhookDelivery {
  return {
    id: 'd',
    event: 'task.completed',
    status_code: 200,
    error: null,
    attempts: 1,
    delivered_at: '2026-10-14T06:00:00Z',
    retrying: false,
    created_at: '2026-10-14T06:00:00Z',
    ...overrides,
  };
}

describe('the events offered', () => {
  it('are every event the server knows, once each', () => {
    // `WebhookEvent` in `domain/enums.py`; the API refuses anything else.
    expect(ALL_EVENTS).toHaveLength(12);
    expect(new Set(ALL_EVENTS).size).toBe(12);
  });

  it('are summed up as every event when all are chosen', () => {
    expect(describeEvents([...ALL_EVENTS])).toBe('every event');
    expect(describeEvents(['task.completed', 'job.accepted'])).toBe('job.accepted, task.completed');
  });
});

describe('who manages webhooks', () => {
  it('takes maintainer rank, as the server requires', () => {
    expect(canManageWebhooks('maintainer')).toBe(true);
    expect(canManageWebhooks('owner')).toBe(true);
    expect(canManageWebhooks('reviewer')).toBe(false);
    expect(canManageWebhooks('annotator')).toBe(false);
    expect(canManageWebhooks(undefined)).toBe(false);
  });
});

describe('a receiver address', () => {
  it('is absolute http or https', () => {
    expect(isTargetUrl('https://hooks.example.com/cv')).toBe(true);
    expect(isTargetUrl(' http://10.0.0.5:8080/hook ')).toBe(true);
    expect(isTargetUrl('hooks.example.com')).toBe(false);
    expect(isTargetUrl('ftp://example.com/')).toBe(false);
    expect(isTargetUrl('')).toBe(false);
  });
});

describe('what a delivery means', () => {
  it('delivered', () => {
    expect(deliveryOutcome(delivery())).toMatchObject({ tone: 'success', label: '200' });
  });

  it('refused by the receiver is not retried, and says why', () => {
    const outcome = deliveryOutcome(
      delivery({ status_code: 401, error: 'bad signature', delivered_at: null }),
    );
    expect(outcome.tone).toBe('danger');
    expect(outcome.label).toBe('401');
    expect(outcome.detail).toContain('answered 401: bad signature');
    expect(outcome.detail).toContain('Not retried');
  });

  it('a struggling receiver is retried', () => {
    const outcome = deliveryOutcome(
      delivery({ status_code: 503, delivered_at: null, retrying: true, attempts: 2 }),
    );
    expect(outcome).toMatchObject({ tone: 'warning', label: '503 · retrying' });
    expect(outcome.detail).toContain('2 attempts so far');
  });

  it('nothing answered, and given up', () => {
    const outcome = deliveryOutcome(
      delivery({ status_code: null, error: 'connection refused', delivered_at: null, attempts: 5 }),
    );
    expect(outcome).toMatchObject({ tone: 'danger', label: 'no answer' });
    expect(outcome.detail).toContain('Nothing answered: connection refused');
    expect(outcome.detail).toContain('Given up after 5 attempts');
  });

  it('429 is not called a refusal', () => {
    const outcome = deliveryOutcome(delivery({ status_code: 429, delivered_at: null, attempts: 5 }));
    expect(outcome.detail).toContain('Given up');
    expect(outcome.detail).not.toContain('refused');
  });
});

describe('the verification snippet', () => {
  it('carries the secret and a constant-time comparison', () => {
    const snippet = verificationSnippet('s3cret');
    expect(snippet).toContain('b"s3cret"');
    expect(snippet).toContain('compare_digest');
  });
});
