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
