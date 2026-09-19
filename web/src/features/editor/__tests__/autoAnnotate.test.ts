import { describe, expect, it } from 'vitest';
import type { InferenceResult, ModelRegistration } from '@/api/types';
import {
  blockedReason,
  describePlan,
  describeUnmatched,
  ignoredClassesNote,
  labelMapping,
  parseClasses,
  plannedClasses,
  summarise,
  unavailableModels,
  usableModels,
} from '../autoAnnotate';

function model(overrides: Partial<ModelRegistration> = {}): ModelRegistration {
  return {
    id: 'm1',
    organization_id: null,
    slug: 'detector',
    name: 'Test Detector',
    description: null,
    provider: 'http',
    kind: 'detector',
    output_labels: ['car', 'person'],
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

const OPEN = model({ id: 'open', name: 'YOLO-World', open_vocabulary: true });
const PROJECT_LABELS = ['car', 'pedestrian'];
const LABELS = [
  { id: 'l-car', name: 'car' },
  { id: 'l-ped', name: 'pedestrian' },
];

describe('which models this panel can run', () => {
  it('keeps detectors and segmenters', () => {
    const models = [model({ id: 'a', kind: 'detector' }), model({ id: 'b', kind: 'segmenter' })];
    expect(usableModels(models).map((m) => m.id)).toEqual(['a', 'b']);
  });

  // A denylist, so a new kind that just runs over frames appears without anybody
  // remembering to allow it.
  it('keeps a kind nobody has thought about yet', () => {
    expect(usableModels([model({ id: 'ocr', kind: 'ocr' })])).toHaveLength(1);
    expect(usableModels([model({ id: 'cls', kind: 'classifier' })])).toHaveLength(1);
  });

  it('drops the kinds that need an interaction this panel does not offer', () => {
    const models = [model({ id: 'i', kind: 'interactor' }), model({ id: 't', kind: 'tracker' })];
    expect(usableModels(models)).toEqual([]);
  });

  it('reports those separately with a reason, rather than hiding them', () => {
    const reported = unavailableModels([
      model({ id: 'i', kind: 'interactor', name: 'SAM' }),
      model({ id: 't', kind: 'tracker', name: 'ByteTrack' }),
    ]);
    expect(reported.map((entry) => entry.model.name)).toEqual(['SAM', 'ByteTrack']);
    expect(reported[0]?.reason).toContain('click');
    expect(reported[1]?.reason).toContain('track');
  });

  it('drops an inactive model from both lists', () => {
    const off = [model({ is_active: false }), model({ id: 'i', kind: 'interactor', is_active: false })];
    expect(usableModels(off)).toEqual([]);
    expect(unavailableModels(off)).toEqual([]);
  });

  it('survives having no models at all', () => {
    expect(usableModels(undefined)).toEqual([]);
    expect(unavailableModels(undefined)).toEqual([]);
  });
});

describe('parseClasses', () => {
  it('splits on commas and trims', () => {
    expect(parseClasses('forklift, pallet ,  crate')).toEqual(['forklift', 'pallet', 'crate']);
  });

  it('drops blanks rather than asking the model for an empty string', () => {
    expect(parseClasses('forklift, , ,pallet,')).toEqual(['forklift', 'pallet']);
    expect(parseClasses('   ')).toEqual([]);
  });

  // Duplicates cost the model work and come back as duplicate boxes over one object.
  it('drops case-duplicates, keeping the first spelling', () => {
    expect(parseClasses('Forklift, forklift, FORKLIFT')).toEqual(['Forklift']);
  });
});

describe('what a run will actually look for', () => {
  it('uses what was typed', () => {
    expect(plannedClasses(OPEN, 'forklift, pallet', PROJECT_LABELS)).toEqual({
      classes: ['forklift', 'pallet'],
      source: 'typed',
    });
  });

  // The behaviour an annotator cannot otherwise guess: an empty box is not "search for
  // nothing", it is "search for what this project is about".
  it('falls back to the project schema when the box is empty', () => {
    expect(plannedClasses(OPEN, '', PROJECT_LABELS)).toEqual({
      classes: ['car', 'pedestrian'],
      source: 'project',
    });
  });

  it('falls back when the box holds only separators', () => {
    expect(plannedClasses(OPEN, ' , , ', PROJECT_LABELS).source).toBe('project');
  });

  it('shows a fixed-head model its own labels, whatever is typed', () => {
    const plan = plannedClasses(model(), 'forklift', PROJECT_LABELS);
    expect(plan).toEqual({ classes: ['car', 'person'], source: 'model' });
  });

  it('says so when no model is chosen', () => {
    expect(plannedClasses(undefined, 'forklift', PROJECT_LABELS).classes).toEqual([]);
  });
});

describe('describePlan', () => {
  it('distinguishes the three sources, so the fallback is visible', () => {
    expect(describePlan({ classes: ['a', 'b'], source: 'typed' })).toBe('Will look for: a, b');
    expect(describePlan({ classes: ['a'], source: 'project' })).toContain("project's labels");
    expect(describePlan({ classes: ['a'], source: 'model' })).toContain('This model detects');
  });

  it('says plainly when there is nothing to look for', () => {
    expect(describePlan({ classes: [], source: 'project' })).toBe('Nothing to look for yet.');
  });
});

describe('blockedReason', () => {
  it('lets a normal open-vocabulary run through', () => {
    expect(blockedReason(OPEN, 'forklift', PROJECT_LABELS)).toBeNull();
    expect(blockedReason(OPEN, '', PROJECT_LABELS)).toBeNull();
  });

  it('lets a normal fixed-head run through', () => {
    expect(blockedReason(model(), '', PROJECT_LABELS)).toBeNull();
  });

  // The dead end worth catching before a round trip: no amount of retyping fixes it, and
  // the answer is "use a different model".
  it('refuses classes on a fixed-head model, naming what it can find', () => {
    const reason = blockedReason(model(), 'forklift', PROJECT_LABELS) ?? '';
    expect(reason).toContain('forklift');
    expect(reason).toContain('car, person');
  });

  it('refuses a fixed-head model that declares nothing', () => {
    expect(blockedReason(model({ output_labels: [] }), '', PROJECT_LABELS)).toContain(
      'declares no labels',
    );
  });

  // An open-vocabulary model asked for nothing finds nothing, which looks like a broken
  // model rather than an empty prompt.
  it('refuses an open run with neither typed classes nor a project schema', () => {
    expect(blockedReason(OPEN, '', [])).toContain('takes the classes to look for as text');
  });

  it('asks for a model when none is chosen', () => {
    expect(blockedReason(undefined, '', PROJECT_LABELS)).toBe('Choose a model.');
  });
});

describe('ignoredClassesNote', () => {
  it('is silent for an open-vocabulary model, which uses the text', () => {
    expect(ignoredClassesNote(OPEN, 'forklift')).toBeNull();
  });

  it('is silent when nothing was typed', () => {
    expect(ignoredClassesNote(model(), '')).toBeNull();
    expect(ignoredClassesNote(model(), ' , ')).toBeNull();
  });

  it('is silent when there is no model yet', () => {
    expect(ignoredClassesNote(undefined, 'forklift')).toBeNull();
  });

  // The state reached by typing classes and then picking a different model: the box
  // unmounts with the text still in it, so the panel has to say what it is really doing.
  it('says what is ignored and what to do instead', () => {
    const note = ignoredClassesNote(model(), 'forklift, pallet') ?? '';
    expect(note).toContain('forklift, pallet');
    expect(note).toContain('are ignored');
    expect(note).toContain('open-vocabulary');
  });

  it('agrees with itself about number', () => {
    expect(ignoredClassesNote(model(), 'forklift')).toContain('is ignored');
    expect(ignoredClassesNote(model(), 'forklift, pallet')).toContain('are ignored');
  });

  // The pairing that keeps this from being a second way to say "blocked": the run goes
  // ahead over the model's own labels.
  it('describes a run that still happens', () => {
    expect(plannedClasses(model(), 'forklift', PROJECT_LABELS).classes).toEqual(['car', 'person']);
  });
});

describe('where predictions will land', () => {
  it('maps a class to the project label of the same name', () => {
    expect(labelMapping(['car', 'pedestrian'], LABELS).mapping).toEqual({
      car: 'l-car',
      pedestrian: 'l-ped',
    });
  });

  // The server drops an unmapped prediction and reports it *after* the run, by which point
  // the model has already spent the time.
  it('names the classes that have nowhere to land', () => {
    const { mapping, unmatched } = labelMapping(['car', 'forklift'], LABELS);
    expect(mapping).toEqual({ car: 'l-car' });
    expect(unmatched).toEqual(['forklift']);
  });

  it('matches case-insensitively, because a person typing is not being precise', () => {
    expect(labelMapping(['CAR'], LABELS).mapping).toEqual({ CAR: 'l-car' });
  });

  it('is deterministic when a schema holds two labels differing only in case', () => {
    const twins = [
      { id: 'first', name: 'Car' },
      { id: 'second', name: 'car' },
    ];
    expect(labelMapping(['car'], twins).mapping).toEqual({ car: 'first' });
  });

  it('maps nothing when the project has no labels', () => {
    const { mapping, unmatched } = labelMapping(['car'], []);
    expect(mapping).toEqual({});
    expect(unmatched).toEqual(['car']);
  });
});

describe('describeUnmatched', () => {
  it('is silent when everything lands', () => {
    expect(describeUnmatched([])).toBeNull();
  });

  it('says what will be discarded and how to keep it', () => {
    const note = describeUnmatched(['forklift']) ?? '';
    expect(note).toContain('forklift');
    expect(note).toContain('discarded');
    expect(note).toContain('Add the label');
  });

  it('agrees with itself about number', () => {
    expect(describeUnmatched(['a'])).toContain('has no matching label');
    expect(describeUnmatched(['a', 'b'])).toContain('have no matching label');
  });
});

describe('summarise', () => {
  function result(overrides: Partial<InferenceResult> = {}): InferenceResult {
    return {
      background_task_id: null,
      shapes: [],
      frames_processed: 0,
      created_shapes: 0,
      created_tags: 0,
      annotation_version: null,
      warnings: [],
      ...overrides,
    };
  }

  it('counts objects and frames', () => {
    expect(summarise(result({ created_shapes: 7, frames_processed: 3 }))).toBe(
      'Added 7 objects across 3 frames, for review.',
    );
  });

  it('gets the singulars right', () => {
    expect(summarise(result({ created_shapes: 1, frames_processed: 1 }))).toBe(
      'Added 1 object across 1 frame, for review.',
    );
  });

  it('counts tags too', () => {
    expect(summarise(result({ created_shapes: 2, created_tags: 1, frames_processed: 2 }))).toBe(
      'Added 2 objects and 1 tag across 2 frames, for review.',
    );
  });

  // "Nothing found" is a real answer and has to read as one, not as a failure.
  it('says plainly when the model found nothing', () => {
    expect(summarise(result({ frames_processed: 4 }))).toBe(
      'The model found nothing on 4 frames.',
    );
    expect(summarise(result())).toBe('The model found nothing.');
  });
});
