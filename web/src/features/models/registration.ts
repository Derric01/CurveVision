/**
 * Registering a model from the Models page: an endpoint and a key, then what it serves.
 *
 * Until this page existed a model could be registered only by `POST /models` with a JSON
 * body naming its kind, its labels and the name its server knows it by — while the editor's
 * Auto-annotate panel told people to "register an inference endpoint" and offered nowhere to
 * do it. The server asks the endpoint what it serves (`POST /models/discover`), so the
 * person registering types a URL and a key, picks a model, and checks what was filled in.
 *
 * Pure, because what is worth pinning is what gets sent: the key only when there is one,
 * the model's own id as `config.model`, a label list that is the model's and not a guess.
 */

import type { DiscoveredModel, ModelKind, ModelRegistrationInput, Role } from '@/api/types';

/** What the form holds. Every field is editable after discovery fills it in. */
export interface RegistrationDraft {
  endpoint: string;
  apiKey: string;
  name: string;
  kind: ModelKind;
  /** Comma separated, as the project form takes labels. */
  labels: string;
  openVocabulary: boolean;
  /** The id the endpoint knows the model by, from discovery; empty when it lists none. */
  model: string;
}

export const MODEL_KINDS: readonly { kind: ModelKind; label: string }[] = [
  { kind: 'detector', label: 'Detector — finds objects in a frame' },
  { kind: 'segmenter', label: 'Segmenter — outlines objects in a frame' },
  { kind: 'classifier', label: 'Classifier — tags a whole frame' },
  { kind: 'ocr', label: 'OCR — reads text' },
  { kind: 'interactor', label: 'Interactor — segments from clicks' },
  { kind: 'tracker', label: 'Tracker — follows objects across frames' },
];

export function emptyDraft(): RegistrationDraft {
  return {
    endpoint: '',
    apiKey: '',
    name: '',
    kind: 'detector',
    labels: '',
    openVocabulary: false,
    model: '',
  };
}

/** The draft with one discovered model's details filled in; the connection is kept. */
export function fillFrom(draft: RegistrationDraft, found: DiscoveredModel): RegistrationDraft {
  return {
    ...draft,
    name: found.name || found.id,
    kind: found.kind,
    labels: found.labels.join(', '),
    openVocabulary: found.open_vocabulary,
    model: found.id,
  };
}

export function parseLabels(text: string): string[] {
  const seen = new Set<string>();
  const labels: string[] = [];
  for (const part of text.split(',')) {
    const label = part.trim();
    if (label && !seen.has(label)) {
      seen.add(label);
      labels.push(label);
    }
  }
  return labels;
}

export function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64);
}

/** The endpoint as the server will call it, or null when it is not an http(s) URL. */
export function normaliseEndpoint(text: string): string | null {
  const trimmed = text.trim();
  try {
    const url = new URL(trimmed);
    return url.protocol === 'http:' || url.protocol === 'https:' ? trimmed : null;
  } catch {
    return null;
  }
}

/** The connection to ask discovery with: the key only when one was typed. */
export function connectionOf(draft: RegistrationDraft): { endpoint: string; api_key?: string } {
  const key = draft.apiKey.trim();
  return { endpoint: draft.endpoint.trim(), ...(key ? { api_key: key } : {}) };
}

/**
 * What stops the draft being registered, in the order a person would fix it; empty when
 * nothing does.
 *
 * A fixed-head model with no labels is refused here rather than at the server, which would
 * take it: the Auto-annotate panel maps a model's labels onto the project's, so a model
 * registered with none can be run and can never have anything it finds kept.
 */
export function problemsWith(draft: RegistrationDraft): string[] {
  const problems: string[] = [];
  if (normaliseEndpoint(draft.endpoint) === null) {
    problems.push('The endpoint must be an http:// or https:// URL.');
  }
  if (!draft.name.trim()) problems.push('Give the model a name.');
  else if (slugify(draft.name).length < 2) {
    problems.push('The name needs at least two letters or digits.');
  }
  if (!draft.openVocabulary && parseLabels(draft.labels).length === 0) {
    problems.push(
      'Name the labels this model returns. Without them nothing it finds can be kept.',
    );
  }
  return problems;
}

export function registrationOf(draft: RegistrationDraft): ModelRegistrationInput {
  const model = draft.model.trim();
  return {
    slug: slugify(draft.name),
    name: draft.name.trim(),
    provider: 'http',
    kind: draft.kind,
    config: { ...connectionOf(draft), ...(model ? { model } : {}) },
    output_labels: parseLabels(draft.labels),
    open_vocabulary: draft.openVocabulary,
  };
}

/** What discovery found, said in a sentence for the form. */
export function describeDiscovery(found: readonly DiscoveredModel[]): string {
  if (found.length === 0) {
    return (
      'Connected. This endpoint does not list its models, so say below what it returns — ' +
      'its kind and the labels it finds.'
    );
  }
  if (found.length === 1) return 'Connected. This endpoint serves one model.';
  return `Connected. This endpoint serves ${found.length} models — pick the one to register.`;
}

/** Ranks the policy engine lets register a model (`MODEL` / `CREATE` floors at maintainer). */
const CAN_REGISTER: ReadonlySet<Role> = new Set<Role>(['maintainer', 'admin', 'owner']);

/**
 * Whether to offer the form. A server older than `role` on an organization sends none; the
 * form is offered then, and the server's 403 is the answer for anybody it should not be.
 */
export function canRegisterModels(role: Role | undefined): boolean {
  return role === undefined || CAN_REGISTER.has(role);
}
