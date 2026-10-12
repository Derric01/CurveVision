import { describe, expect, it } from 'vitest';
import type { DiscoveredModel } from '@/api/types';
import {
  HOSTED_SERVICES,
  connectionProblems,
  defaultName,
  emptyHostedDraft,
  filterModels,
  firstChoice,
  hostedConnection,
  hostedRegistration,
  privacyNote,
  switchService,
} from '../services';

function found(id: string, name = id): DiscoveredModel {
  return {
    id,
    name,
    kind: 'detector',
    labels: [],
    shape_types: ['rectangle'],
    open_vocabulary: true,
    description: null,
  };
}

describe('the services offered', () => {
  it('include every service the request named, by its own request shape', () => {
    const byId = Object.fromEntries(HOSTED_SERVICES.map((service) => [service.id, service]));
    expect(byId.anthropic?.provider).toBe('anthropic');
    for (const id of ['openai', 'openrouter', 'xai', 'gemini']) {
      expect(byId[id]?.provider).toBe('openai_compatible');
      expect(byId[id]?.baseUrl).toMatch(/^https:\/\//);
    }
  });
});

describe('connecting', () => {
  it('sends Claude its key and no base URL', () => {
    const draft = { ...emptyHostedDraft('anthropic'), apiKey: ' sk-ant-1 ' };
    expect(hostedConnection(draft)).toEqual({
      provider: 'anthropic',
      config: { api_key: 'sk-ant-1' },
    });
  });

  it('sends an OpenAI-compatible service its own base URL, whatever was typed', () => {
    const draft = { ...emptyHostedDraft('openai'), apiKey: 'sk-1', baseUrl: 'http://elsewhere' };
    expect(hostedConnection(draft)).toEqual({
      provider: 'openai_compatible',
      config: { api_key: 'sk-1', base_url: 'https://api.openai.com/v1' },
    });
  });

  it('sends the typed base URL where it is the person’s to type, and no key when none', () => {
    const draft = { ...emptyHostedDraft('ollama'), baseUrl: 'http://gpu-box:11434/v1' };
    expect(hostedConnection(draft)).toEqual({
      provider: 'openai_compatible',
      config: { base_url: 'http://gpu-box:11434/v1' },
    });
    expect(connectionProblems(draft)).toEqual([]);
  });

  it('needs a key where the service does, and a real URL where it is typed', () => {
    expect(connectionProblems(emptyHostedDraft('xai'))).toEqual(['Paste your xAI API key.']);
    expect(connectionProblems({ ...emptyHostedDraft('other'), baseUrl: 'api.example.com' })).toEqual(
      ['The base URL must be an http:// or https:// URL.'],
    );
  });

  it('does not carry a key from one service to another', () => {
    const typed = { ...emptyHostedDraft('openai'), apiKey: 'sk-openai' };
    expect(switchService(typed, 'anthropic').apiKey).toBe('');
    expect(switchService(typed, 'anthropic').baseUrl).toBe('');
    expect(switchService(typed, 'openrouter').baseUrl).toBe('https://openrouter.ai/api/v1');
  });
});

describe('choosing a model', () => {
  it('offers Anthropic’s default first when the key lists it', () => {
    const models = [found('claude-haiku-5-5'), found('claude-opus-5-5')];
    expect(firstChoice('anthropic', models)).toBe('claude-opus-5-5');
  });

  it('leaves the choice to the person when several are listed and none is preferred', () => {
    expect(firstChoice('openai', [found('a'), found('b')])).toBe('');
    expect(firstChoice('openai', [found('only')])).toBe('only');
  });

  it('names it after the service and the model', () => {
    expect(defaultName('xai', found('grok-vision', 'Grok Vision'))).toBe('xAI Grok · Grok Vision');
    expect(defaultName('anthropic', found('claude-opus-5-5', 'Claude Opus 5.5'))).toBe(
      'Claude · Claude Opus 5.5',
    );
  });

  it('filters a long list by id or name', () => {
    const models = [found('openai/gpt-x', 'GPT X'), found('qwen/qwen-vl', 'Qwen VL')];
    expect(filterModels(models, 'qwen').map((model) => model.id)).toEqual(['qwen/qwen-vl']);
    expect(filterModels(models, '  ')).toHaveLength(2);
  });
});

describe('what is registered', () => {
  it('is an open-vocabulary detector holding the key, the URL and the model', () => {
    const draft = {
      ...emptyHostedDraft('openrouter'),
      apiKey: 'sk-or-1',
      model: 'qwen/qwen-vl',
      name: 'OpenRouter · Qwen VL',
    };
    expect(hostedRegistration(draft)).toEqual({
      slug: 'openrouter-qwen-qwen-vl',
      name: 'OpenRouter · Qwen VL',
      provider: 'openai_compatible',
      kind: 'detector',
      config: { api_key: 'sk-or-1', base_url: 'https://openrouter.ai/api/v1', model: 'qwen/qwen-vl' },
      output_labels: [],
      open_vocabulary: true,
    });
  });
});

describe('where the frames go', () => {
  it('is said, and differently for a model on this computer', () => {
    expect(privacyNote('anthropic')).toMatch(/sent to Anthropic, and billed to this key/);
    expect(privacyNote('ollama')).toMatch(/stay on this computer/);
  });
});
