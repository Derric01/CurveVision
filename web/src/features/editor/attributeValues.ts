/**
 * Reading and writing one object's attribute values in the editor.
 *
 * The server validates what it receives (`validate_attributes`): a checkbox takes a
 * boolean, a number anything `float()` accepts, a select or radio one of its options, and
 * text anything. What is decided here is how a control's raw input becomes one of those —
 * and, as importantly, what *clears* a value, because an attribute left unset and one set
 * to an empty string are different things to an exported dataset.
 */

import type { AttributeDefinition } from '@/api/types';

/**
 * The value to store for what a control holds, or `undefined` to clear it.
 *
 * A blank text or number box clears rather than storing `""`: the server would store the
 * empty string for text, and refuse it for a number. A number that does not parse is
 * `null` — the caller keeps showing what was typed and sends nothing until it does.
 */
export function valueFromInput(
  definition: AttributeDefinition,
  raw: string | boolean,
): unknown {
  if (definition.attribute_type === 'checkbox') return raw === true || raw === 'true';
  const text = String(raw);
  if (text.trim() === '') return undefined;
  if (definition.attribute_type === 'number') {
    const parsed = Number(text);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return text;
}

/**
 * What a control shows for a stored value.
 *
 * A checkbox default is stored as the string `"true"` or `"false"` (the server fills a
 * default in without coercing it), so both spellings of a boolean have to read as one.
 */
export function inputFromValue(
  definition: AttributeDefinition,
  value: unknown,
): string | boolean {
  if (definition.attribute_type === 'checkbox') {
    return value === true || (typeof value === 'string' && value.toLowerCase() === 'true');
  }
  return value === undefined || value === null ? '' : String(value);
}

/**
 * The values a newly drawn object of a label starts with.
 *
 * Every default, typed, which is CVAT's `appendDefaultAttributes`: the panel then shows
 * what the save will record, and a checkbox default is sent as the boolean an explicit value
 * is stored as, rather than filled in server-side as the string `"false"`. And `false` for a
 * required checkbox with no default — an unticked box is what the control shows, so without
 * it the object displayed a value it was then refused for not having. A required select,
 * radio, text or number with no default gets nothing: choosing is the annotator's job, and
 * the save names the object until they have.
 */
export function initialAttributes(
  definitions: readonly AttributeDefinition[],
): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const definition of definitions) {
    const fallback = definition.default_value;
    if (fallback === null || fallback === undefined) {
      if (definition.required && definition.attribute_type === 'checkbox') {
        values[definition.name] = false;
      }
      continue;
    }
    const typed = inputFromValue(definition, fallback);
    if (definition.attribute_type === 'checkbox') values[definition.name] = typed;
    else if (definition.attribute_type === 'number') {
      const parsed = valueFromInput(definition, fallback);
      if (typeof parsed === 'number') values[definition.name] = parsed;
    } else if (definition.attribute_type === 'text' || definition.values.includes(fallback)) {
      // A select default outside its options, which an older server accepted, is left to
      // the server to fill in as it always has rather than sent and refused.
      values[definition.name] = fallback;
    }
  }
  return values;
}

/**
 * A required attribute the object has no value for, which its save will be refused over.
 *
 * Only for marking the control: whether a save is refused is still the server's answer.
 * A default counts as a value, since the server fills it in (`validate_attributes`).
 */
export function isMissing(definition: AttributeDefinition, value: unknown): boolean {
  return (
    definition.required &&
    (definition.default_value === null || definition.default_value === undefined) &&
    (value === undefined || value === null)
  );
}

/**
 * The attributes the panel offers for a label, in the schema's order.
 *
 * `required` is marked on the control rather than enforced here: the server is what
 * refuses a save without it, and a second copy of that rule in the browser would be the
 * one that drifted.
 */
export function attributesFor(
  labels: readonly { id: string; attributes: AttributeDefinition[]; children?: { id: string; attributes: AttributeDefinition[] }[] }[],
  labelId: string,
): AttributeDefinition[] {
  for (const label of labels) {
    if (label.id === labelId) return [...label.attributes].sort((a, b) => a.position - b.position);
    for (const child of label.children ?? []) {
      if (child.id === labelId) return [...child.attributes].sort((a, b) => a.position - b.position);
    }
  }
  return [];
}

/** For each label, the names of its attributes that may change from frame to frame. */
export function mutableNames(
  labels: readonly { id: string; attributes: AttributeDefinition[] }[],
): Map<string, ReadonlySet<string>> {
  return new Map(
    labels.map((label) => [
      label.id,
      new Set(label.attributes.filter((a) => a.mutable).map((a) => a.name)),
    ]),
  );
}
