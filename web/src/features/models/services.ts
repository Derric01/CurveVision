/**
 * Hosted AI services a model can be added from with nothing but an API key (ADR 0010).
 *
 * Two request shapes cover them: Anthropic's own (`provider: 'anthropic'`) and the OpenAI
 * Chat Completions shape (`provider: 'openai_compatible'`), which needs only a base URL to
 * reach any service that speaks it. The base URLs are the one thing written down here, and
 * they can go stale — *Other* takes any URL. **Model names are never written down**: they
 * come from the service's own model list when the key connects, so a new model appears
 * without a release of this file. The one exception is the model offered first for Claude,
 * which is Anthropic's current default.
 *
 * Pure, because what is worth pinning is what is sent: the key only to the service it
 * belongs to, a base URL only where the request shape needs one, and an honest note about
 * where the frames go.
 */

import type { DiscoveredModel, ModelRegistrationInput } from '@/api/types';
import { normaliseEndpoint, slugify } from './registration';

export type HostedProvider = 'anthropic' | 'openai_compatible';

export interface HostedService {
  id: string;
  /** What the picker shows. */
  name: string;
  /** Who the frames go to, for the privacy note. */
  company: string;
  provider: HostedProvider;
  /** The Chat Completions base URL; null for Anthropic, whose SDK knows its own. */
  baseUrl: string | null;
  /** Whether the base URL is the person's to type (*Other*, a local server). */
  editableUrl: boolean;
  keyRequired: boolean;
  /** Served on this computer: frames do not leave it. */
  local: boolean;
  /** Offered first when the service lists it. */
  preferred: readonly string[];
}

export const HOSTED_SERVICES: readonly HostedService[] = [
  {
    id: 'anthropic',
    name: 'Claude (Anthropic)',
    company: 'Anthropic',
    provider: 'anthropic',
    baseUrl: null,
    editableUrl: false,
    keyRequired: true,
    local: false,
    preferred: ['claude-opus-5-5'],
  },
  service('openai', 'OpenAI', 'OpenAI', 'https://api.openai.com/v1'),
  service('gemini', 'Google Gemini', 'Google', 'https://generativelanguage.googleapis.com/v1beta/openai'),
  service('xai', 'xAI Grok', 'xAI', 'https://api.x.ai/v1'),
  service('openrouter', 'OpenRouter (hundreds of models)', 'OpenRouter and the model it routes to', 'https://openrouter.ai/api/v1'),
  service('mistral', 'Mistral', 'Mistral AI', 'https://api.mistral.ai/v1'),
  service('groq', 'Groq', 'Groq', 'https://api.groq.com/openai/v1'),
  service('together', 'Together AI', 'Together AI', 'https://api.together.xyz/v1'),
  {
    ...service('ollama', 'Ollama (on this computer)', 'Ollama', 'http://localhost:11434/v1'),
    editableUrl: true,
    keyRequired: false,
    local: true,
  },
  {
    ...service('other', 'Other OpenAI-compatible service', 'that service', ''),
    editableUrl: true,
    keyRequired: false,
  },
];

function service(id: string, name: string, company: string, baseUrl: string): HostedService {
  return {
    id,
    name,
    company,
    provider: 'openai_compatible',
    baseUrl,
    editableUrl: false,
    keyRequired: true,
    local: false,
    preferred: [],
  };
}

export function hostedService(id: string): HostedService {
  return HOSTED_SERVICES.find((candidate) => candidate.id === id) ?? HOSTED_SERVICES[0]!;
}

/** What the form holds for a hosted model. */
export interface HostedDraft {
  serviceId: string;
  apiKey: string;
  /** Only read where the service's URL is editable. */
  baseUrl: string;
  model: string;
  name: string;
}

export function emptyHostedDraft(serviceId = HOSTED_SERVICES[0]!.id): HostedDraft {
  return {
    serviceId,
    apiKey: '',
    baseUrl: hostedService(serviceId).baseUrl ?? '',
    model: '',
    name: '',
  };
}

/** The draft for another service: its URL, and nothing carried over from the last one. */
export function switchService(draft: HostedDraft, serviceId: string): HostedDraft {
  return { ...emptyHostedDraft(serviceId), apiKey: draft.serviceId === serviceId ? draft.apiKey : '' };
}

/** What discovery is asked with, and what the registration keeps in `config`. */
export function hostedConnection(draft: HostedDraft): {
  provider: HostedProvider;
  config: { api_key?: string; base_url?: string };
} {
  const chosen = hostedService(draft.serviceId);
  const key = draft.apiKey.trim();
  const baseUrl = chosen.editableUrl ? draft.baseUrl.trim() : chosen.baseUrl;
  return {
    provider: chosen.provider,
    config: {
      ...(key ? { api_key: key } : {}),
      ...(chosen.provider === 'openai_compatible' && baseUrl ? { base_url: baseUrl } : {}),
    },
  };
}

/** What stops the key being tried; empty when nothing does. */
export function connectionProblems(draft: HostedDraft): string[] {
  const chosen = hostedService(draft.serviceId);
  const problems: string[] = [];
  if (chosen.keyRequired && !draft.apiKey.trim()) problems.push(`Paste your ${chosen.company} API key.`);
  if (chosen.editableUrl && normaliseEndpoint(draft.baseUrl) === null) {
    problems.push('The base URL must be an http:// or https:// URL.');
  }
  return problems;
}

/** The model offered first: the service's preferred one when listed, else the only one. */
export function firstChoice(serviceId: string, found: readonly DiscoveredModel[]): string {
  const listed = new Set(found.map((model) => model.id));
  const preferred = hostedService(serviceId).preferred.find((id) => listed.has(id));
  if (preferred) return preferred;
  return found.length === 1 ? found[0]!.id : '';
}

/** The name annotators pick it by, until somebody types another. */
export function defaultName(serviceId: string, model: DiscoveredModel | undefined): string {
  if (!model) return '';
  const chosen = hostedService(serviceId);
  const company = chosen.id === 'other' ? '' : `${chosen.name.replace(/ \(.*\)$/, '')} · `;
  return `${company}${model.name}`;
}

export function hostedRegistration(draft: HostedDraft): ModelRegistrationInput {
  const { provider, config } = hostedConnection(draft);
  const name = draft.name.trim() || draft.model;
  return {
    // From the model's id, so adding the same model twice is a clear 409, not a twin.
    slug: slugify(`${draft.serviceId}-${draft.model}`) || slugify(draft.serviceId),
    name,
    provider,
    // A general vision-language model draws boxes; nothing finer is asked of it.
    kind: 'detector',
    config: { ...config, model: draft.model },
    // It is told what to find, by name: the project's own labels unless an annotator
    // types others.
    output_labels: [],
    open_vocabulary: true,
  };
}

/** Where the frames go, said where the model is added rather than discovered later. */
export function privacyNote(serviceId: string): string {
  const chosen = hostedService(serviceId);
  if (chosen.local) return 'Frames stay on this computer: Ollama runs the model here.';
  return (
    `Each frame you run it on is sent to ${chosen.company}, and billed to this key. ` +
    'Its boxes are a general model’s guesses: they arrive as suggestions to accept, ' +
    'fix or reject.'
  );
}

export function filterModels(
  found: readonly DiscoveredModel[],
  text: string,
): DiscoveredModel[] {
  const needle = text.trim().toLowerCase();
  if (!needle) return [...found];
  return found.filter(
    (model) =>
      model.id.toLowerCase().includes(needle) || model.name.toLowerCase().includes(needle),
  );
}

/** How a registered model's provider reads on its card. */
export function providerLabel(provider: string): string {
  switch (provider) {
    case 'anthropic':
      return 'Claude';
    case 'openai_compatible':
      return 'hosted, OpenAI-compatible';
    case 'http':
      return 'model server';
    default:
      return provider;
  }
}
