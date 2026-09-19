/**
 * Running a model over a job, from the editor.
 *
 * `api.models`, `api.runInference` and `api.decideSuggestions` have been on the client since
 * the first web iteration with nothing calling them: the editor had no AI affordance at all,
 * while the server grew a whole inference path. This is the logic behind the panel that
 * closes that, kept pure so the decisions can be tested in a runtime with no DOM.
 *
 * **Two of these deliberately mirror rules the server also enforces**, and that is worth
 * being explicit about rather than leaving as accidental duplication:
 *
 * - `blockedReason` repeats `services/inference.resolve_classes`. The server is still the
 *   authority and still returns 422; this exists so the annotator learns *before* pressing a
 *   button that this model cannot look for what they typed.
 * - `labelMapping` anticipates what `persist_predictions` will drop. The server reports
 *   unmapped labels *after* the run, by which point the model has already spent the time.
 *
 * Both are stated on screen as what *will* happen, never as what did.
 */

import type { InferenceResult, Label, ModelRegistration } from '@/api/types';

/**
 * Model kinds this panel cannot drive, and why.
 *
 * A denylist rather than an allowlist on purpose: a new kind that produces shapes or tags
 * from a plain "run over these frames" should appear here without anybody remembering to
 * add it, and a kind that genuinely needs a different interaction should have to be named.
 */
const NEEDS_ANOTHER_INTERACTION: Record<string, string> = {
  // Wants a click or a box on the canvas to segment around.
  interactor: 'needs a click on the canvas',
  // Wants an existing object to follow, not a blank frame.
  tracker: 'needs an existing track to follow',
};

/** Active models this panel can actually run. */
export function usableModels(models: readonly ModelRegistration[] | undefined): ModelRegistration[] {
  return (models ?? []).filter(
    (model) => model.is_active && !(model.kind in NEEDS_ANOTHER_INTERACTION),
  );
}

/** Models that exist but need an interaction this panel does not offer, with the reason. */
export function unavailableModels(
  models: readonly ModelRegistration[] | undefined,
): { model: ModelRegistration; reason: string }[] {
  return (models ?? [])
    .filter((model) => model.is_active && model.kind in NEEDS_ANOTHER_INTERACTION)
    .map((model) => ({ model, reason: NEEDS_ANOTHER_INTERACTION[model.kind] as string }));
}

/** Comma-separated text to a clean class list: trimmed, no blanks, no case-duplicates. */
export function parseClasses(text: string): string[] {
  const seen = new Set<string>();
  const classes: string[] = [];
  for (const part of text.split(',')) {
    const name = part.trim();
    if (!name) continue;
    const key = name.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    classes.push(name);
  }
  return classes;
}

export type ClassSource = 'typed' | 'project' | 'model';

export interface ClassPlan {
  classes: string[];
  source: ClassSource;
}

/**
 * What this run will actually look for, and where that came from.
 *
 * The `project` case is the one worth surfacing: an open-vocabulary run with an empty box
 * searches for the project's own labels, and an annotator who cannot see that has no reason
 * to believe the empty box will do anything at all.
 */
export function plannedClasses(
  model: ModelRegistration | undefined,
  text: string,
  projectLabels: readonly string[],
): ClassPlan {
  if (!model) return { classes: [], source: 'model' };
  if (!model.open_vocabulary) return { classes: [...model.output_labels], source: 'model' };
  const typed = parseClasses(text);
  if (typed.length > 0) return { classes: typed, source: 'typed' };
  return { classes: [...projectLabels], source: 'project' };
}

/** One line under the class box: what will be searched for, and why those. */
export function describePlan(plan: ClassPlan): string {
  if (plan.classes.length === 0) {
    return 'Nothing to look for yet.';
  }
  const list = plan.classes.join(', ');
  switch (plan.source) {
    case 'typed':
      return `Will look for: ${list}`;
    case 'project':
      return `Will look for the project's labels: ${list}`;
    default:
      return `This model detects: ${list}`;
  }
}

/**
 * Why this run cannot happen, or `null` when it can.
 *
 * Mirrors the server's `resolve_classes`, whose 422 is still the authority. The point of
 * repeating it is that a dead end should be visible before a button is pressed, not after a
 * round trip — especially the fixed-head case, where the answer is "use a different model"
 * and no amount of retyping will help.
 */
export function blockedReason(
  model: ModelRegistration | undefined,
  text: string,
  projectLabels: readonly string[],
): string | null {
  if (!model) return 'Choose a model.';

  const typed = parseClasses(text);
  if (!model.open_vocabulary) {
    if (typed.length > 0) {
      const known = model.output_labels.length
        ? model.output_labels.join(', ')
        : 'nothing it has declared';
      return `${model.name} has a fixed label space and cannot be asked for ${typed.join(
        ', ',
      )}. It detects: ${known}.`;
    }
    if (model.output_labels.length === 0) {
      return `${model.name} declares no labels, so there is nothing for it to find.`;
    }
    return null;
  }

  if (typed.length === 0 && projectLabels.length === 0) {
    return `${model.name} takes the classes to look for as text, and neither this box nor the project's labels name any.`;
  }
  return null;
}

/**
 * The note for class text a model cannot use, or `null` when there is none.
 *
 * An annotator types classes for an open-vocabulary model and then picks a fixed-head one
 * out of the same list: the box unmounts with the text still in it. Both obvious responses
 * are wrong. Saying nothing runs a different search than the one on screen a moment ago;
 * refusing the run is an error quoting text they can no longer see, let alone clear. So the
 * run goes ahead over the model's own labels, and this says why the typed ones are not in it.
 */
export function ignoredClassesNote(
  model: ModelRegistration | undefined,
  text: string,
): string | null {
  if (!model || model.open_vocabulary) return null;
  const typed = parseClasses(text);
  if (typed.length === 0) return null;
  const list = typed.join(', ');
  const [is, it] = typed.length === 1 ? ['is', 'it'] : ['are', 'them'];
  return `${model.name} has a fixed label space, so ${list} ${is} ignored here. Pick an open-vocabulary model to look for ${it}.`;
}

export interface LabelMapping {
  /** Predicted class name → project label id, for the ones that have somewhere to land. */
  mapping: Record<string, string>;
  /** Classes the model may return that no project label matches. These are dropped. */
  unmatched: string[];
}

/**
 * Where each predicted class will be filed, and which will be thrown away.
 *
 * The server drops a prediction whose label it was given no mapping for, and reports it
 * afterwards — deliberately, because inventing labels from a model's vocabulary is how a
 * label schema rots. Matching by name against labels that **already exist** is not
 * inventing one, so this builds the obvious mapping and, more usefully, says up front which
 * classes have nowhere to go.
 *
 * Matching is case-insensitive because "Forklift" and "forklift" are the same request from a
 * person; where a schema really does contain both, the first in schema order wins and the
 * behaviour is at least deterministic.
 */
export function labelMapping(
  classes: readonly string[],
  labels: readonly Pick<Label, 'id' | 'name'>[],
): LabelMapping {
  const byName = new Map<string, string>();
  for (const label of labels) {
    const key = label.name.toLowerCase();
    if (!byName.has(key)) byName.set(key, label.id);
  }

  const mapping: Record<string, string> = {};
  const unmatched: string[] = [];
  for (const name of classes) {
    const id = byName.get(name.toLowerCase());
    if (id) mapping[name] = id;
    else unmatched.push(name);
  }
  return { mapping, unmatched };
}

/** The warning shown before a run, when some of what it finds has nowhere to land. */
export function describeUnmatched(unmatched: readonly string[]): string | null {
  if (unmatched.length === 0) return null;
  const list = unmatched.join(', ');
  const verb = unmatched.length === 1 ? 'has' : 'have';
  return `${list} ${verb} no matching label in this project, so anything found will be discarded. Add the label first to keep it.`;
}

/** What happened, in one line. */
export function summarise(result: InferenceResult): string {
  const { created_shapes: shapes, created_tags: tags, frames_processed: frames } = result;
  if (shapes === 0 && tags === 0) {
    return frames === 0
      ? 'The model found nothing.'
      : `The model found nothing on ${frames} ${frames === 1 ? 'frame' : 'frames'}.`;
  }
  const parts: string[] = [];
  if (shapes > 0) parts.push(`${shapes} ${shapes === 1 ? 'object' : 'objects'}`);
  if (tags > 0) parts.push(`${tags} ${tags === 1 ? 'tag' : 'tags'}`);
  return `Added ${parts.join(' and ')} across ${frames} ${frames === 1 ? 'frame' : 'frames'}, for review.`;
}
