/**
 * Editing the attributes of a label that already exists.
 *
 * Attribute values are stored on each annotation keyed by attribute **name**, and the
 * server rejects a key its schema does not declare. So a careless edit does not fail
 * itself — it fails the next save of every annotation carrying the old value. The server
 * refuses those edits (`update_label` in `services/projects.py`), and this module follows
 * the same rules so that the form does not offer what will only be refused:
 *
 * * An attribute that already exists keeps its **type** and whether it is **mutable**.
 *   Those two controls are shown fixed; removing the attribute and adding a new one is how
 *   to change them. The rule is CVAT's, and it holds whether or not anything is recorded.
 * * A select or radio attribute that already exists keeps its **options**. New ones can be
 *   added; the saved ones are shown fixed.
 * * **Renaming or removing** an existing attribute is offered, and the server refuses it
 *   while any annotation records a value under that name. That refusal names the count
 *   and is shown as it arrives — whether a value is recorded is not something the browser
 *   knows.
 *
 * What is checked here and not left to the server: a blank or repeated name, a select
 * with no options, and a default the attribute would refuse. Those are 422s, which reach
 * the screen only as "One or more fields are invalid" — and all of them are knowable from
 * the form alone, unlike a label's duplicate name, which is a comparison with other rows.
 *
 * `required` is **carried through, not offered**. The editor cannot yet set an attribute's
 * value on a shape, so a required attribute with no default would make its label
 * impossible to draw with from the application at all.
 */

import type { AttributeDefinition, AttributePayload } from '@/api/types';
import { normaliseLabelName } from './labelSchema';

export type AttributeType = AttributeDefinition['attribute_type'];

/** In the order the type picker lists them. */
export const ATTRIBUTE_TYPES: readonly AttributeType[] = [
  'checkbox',
  'select',
  'radio',
  'text',
  'number',
];

/** One attribute as the form holds it, which is not quite as the server does. */
export interface AttributeDraft {
  /**
   * The server's id, or `null` for an attribute this form is adding. Sending the id back
   * is what keeps an existing definition — and the values recorded under it — rather than
   * replacing it with a new one of the same name.
   */
  id: string | null;
  name: string;
  attribute_type: AttributeType;
  /** Options the server already has. Shown fixed: they may be added to, not removed. */
  savedOptions: string[];
  /** Options typed into the form, comma separated. */
  addedOptions: string;
  /** `''` means no default. */
  default_value: string;
  mutable: boolean;
  /** Carried through untouched; see the module comment for why it is not offered. */
  required: boolean;
}

export function draftFromAttribute(attribute: AttributeDefinition): AttributeDraft {
  return {
    id: attribute.id,
    name: attribute.name,
    attribute_type: attribute.attribute_type,
    savedOptions: [...attribute.values],
    addedOptions: '',
    // The server accepts a checkbox default in either case and the picker offers
    // lowercase, so "False" would otherwise match nothing and be dropped on save.
    default_value:
      attribute.attribute_type === 'checkbox'
        ? (attribute.default_value ?? '').toLowerCase()
        : (attribute.default_value ?? ''),
    mutable: attribute.mutable,
    required: attribute.required,
  };
}

/** A new attribute. A checkbox, because it is the one type that needs nothing else. */
export function blankAttribute(): AttributeDraft {
  return {
    id: null,
    name: '',
    attribute_type: 'checkbox',
    savedOptions: [],
    addedOptions: '',
    default_value: '',
    mutable: false,
    required: false,
  };
}

/** Whether this is an attribute the server already has, and so one whose type is fixed. */
export function isSaved(draft: AttributeDraft): boolean {
  return draft.id !== null;
}

export function needsOptions(type: AttributeType): boolean {
  return type === 'select' || type === 'radio';
}

/**
 * Options typed as `"red, blue"`, as a list.
 *
 * Trimmed, empties dropped (a trailing comma is not an option called `""`), and repeats
 * dropped, since the server checks a value against the list and a repeat would only be a
 * second copy of the same answer.
 */
export function parseOptions(raw: string): string[] {
  const seen = new Set<string>();
  const options: string[] = [];
  for (const part of raw.split(',')) {
    const option = part.trim();
    if (option && !seen.has(option)) {
      seen.add(option);
      options.push(option);
    }
  }
  return options;
}

/**
 * Every option the attribute will have: the saved ones first, in their order, then the
 * new ones. Only a select or radio has options; any other type sends back exactly what the
 * server had, untouched.
 */
export function optionsOf(draft: AttributeDraft): string[] {
  if (!needsOptions(draft.attribute_type)) return draft.savedOptions;
  const added = parseOptions(draft.addedOptions).filter(
    (option) => !draft.savedOptions.includes(option),
  );
  return [...draft.savedOptions, ...added];
}

/**
 * What the default may be chosen from, or `null` where it is typed freely.
 *
 * A checkbox's default is `"true"` or `"false"` because that is how the server stores a
 * default — as a string — and how it reads one back.
 */
export function defaultChoices(draft: AttributeDraft): string[] | null {
  if (needsOptions(draft.attribute_type)) return optionsOf(draft);
  if (draft.attribute_type === 'checkbox') return ['false', 'true'];
  return null;
}

/**
 * The first reason these attributes cannot be saved, or `null` if they can.
 *
 * A sentence rather than a flag, because it is shown beside a disabled Save button and a
 * button that is simply grey does not say what to fix.
 */
export function attributeProblem(drafts: readonly AttributeDraft[]): string | null {
  const names = new Set<string>();
  for (const draft of drafts) {
    const name = normaliseLabelName(draft.name);
    if (!name) return 'Every attribute needs a name.';
    if (names.has(name)) return `Two attributes are named "${name}".`;
    names.add(name);
    if (needsOptions(draft.attribute_type) && optionsOf(draft).length === 0) {
      return `"${name}" needs at least one option.`;
    }
    const fallback = draft.default_value.trim();
    if (draft.attribute_type === 'number' && fallback && Number.isNaN(Number(fallback))) {
      return `The default of "${name}" must be a number.`;
    }
  }
  return null;
}

/**
 * The attribute as `PUT` accepts it.
 *
 * A default that is no longer one of the choices — the type was changed on a new
 * attribute, say, from checkbox to select — is dropped rather than sent, because the
 * server would refuse the whole label over it.
 */
export function draftToPayload(draft: AttributeDraft, position: number): AttributePayload {
  const choices = defaultChoices(draft);
  const fallback = draft.default_value.trim();
  const defaultValue = !fallback || (choices && !choices.includes(fallback)) ? null : fallback;
  return {
    ...(draft.id !== null ? { id: draft.id } : {}),
    name: normaliseLabelName(draft.name),
    attribute_type: draft.attribute_type,
    values: optionsOf(draft),
    default_value: defaultValue,
    mutable: draft.mutable,
    required: draft.required,
    position,
  };
}
