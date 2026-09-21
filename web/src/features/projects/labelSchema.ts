/**
 * Adding to and removing from a project's label schema after the project exists.
 *
 * `POST`/`DELETE /projects/{id}/labels` have existed since the initial schema, the policy
 * engine gates them, and `api.createLabel` sat in the web client **with no caller** — so a
 * project that turned out to need a `van` class alongside `car` and `truck` could only get
 * one from the SDK, the CLI or curl. Label schemas are not knowable in advance; that is the
 * whole reason those endpoints were written.
 *
 * Pure, because these are the rules worth pinning without a DOM. What is deliberately
 * *not* here: any check for a duplicate name, or for whether this person may change the
 * schema at all. Both are the server's answers — the name comparison is case-sensitive and
 * lives in one place, and permission is the policy engine's
 * ([ADR 0002](../../../docs/adr/0002-in-process-policy-engine.md)) — and a second copy in
 * the browser would be a second thing to keep true.
 */

import type { Label, LabelPayload } from '@/api/types';

/** What `labelToPayload` needs of a label: everything `PUT` would otherwise destroy. */
export type LabelForEditing = Pick<
  Label,
  'name' | 'color' | 'position' | 'allowed_shape_types' | 'skeleton_edges' | 'attributes'
>;

/**
 * Colours to propose, in order, for a label being added.
 *
 * Distinguishable from one another on the dark canvas, and distinct from `LabelIn`'s
 * server-side default of `#38bdf8` — a schema where every class is the same blue is a
 * schema an annotator cannot read at a glance.
 */
const PALETTE = [
  '#ef4444',
  '#f97316',
  '#eab308',
  '#22c55e',
  '#14b8a6',
  '#38bdf8',
  '#6366f1',
  '#a855f7',
  '#ec4899',
] as const;

/**
 * A colour for the next label, avoiding the ones already in the schema.
 *
 * Falls back to cycling once the palette is exhausted rather than returning nothing: at
 * ten labels a repeat is unavoidable, and an empty colour field would be worse than a
 * repeated swatch the person can change.
 */
export function nextLabelColor(existing: readonly { color: string }[]): string {
  const used = new Set(existing.map((label) => label.color.toLowerCase()));
  // The last `?? PALETTE[0]` is for the type checker rather than for reality: the index is
  // always in range, and a literal fallback says so more cheaply than an assertion.
  return PALETTE.find((color) => !used.has(color)) ?? PALETTE[existing.length % PALETTE.length]
    ?? PALETTE[0];
}

/**
 * What is actually sent for a name typed into the box.
 *
 * Trimmed, and internal runs of whitespace collapsed: " school  bus " and "school bus" are
 * the same class to everybody except a string comparison, and the server's duplicate check
 * is a string comparison.
 */
export function normaliseLabelName(raw: string): string {
  return raw.trim().replace(/\s+/g, ' ');
}

/** Whether Add can be pressed: a label needs a name, and whitespace is not one. */
export function canAddLabel(raw: string): boolean {
  return normaliseLabelName(raw).length > 0;
}

/**
 * A label read from the API, ready to be sent back to `PUT` with some fields changed.
 *
 * Two things make this worth a function rather than a spread at the call site.
 *
 * **`PUT` replaces the whole label.** Everything not sent is destroyed: an omitted
 * `position` becomes 0 and moves the label to the top of the schema, an omitted
 * `allowed_shape_types` lifts its shape restriction, and an omitted `attributes` deletes
 * every attribute definition on it — along with the schema that validates the values
 * already stored on annotations. Building the payload *from the label* rather than from
 * the edited fields is what makes an edit safe.
 *
 * **And the label cannot simply be sent back as it arrived.** `LabelIn` is strict, so
 * `project_id` and `parent_id` — which `LabelOut` reports and the server owns — are
 * rejected outright with a 422. `children` goes too: `update_label` does not touch
 * sub-labels, and a child carries the same two forbidden fields.
 */
export function labelToPayload(
  label: LabelForEditing,
  changes: Partial<Pick<LabelPayload, 'name' | 'color'>> = {},
): LabelPayload {
  return {
    name: label.name,
    color: label.color,
    position: label.position,
    allowed_shape_types: label.allowed_shape_types,
    skeleton_edges: label.skeleton_edges,
    attributes: label.attributes,
    ...changes,
  };
}

/**
 * The warning shown beside a label's delete control.
 *
 * The count comes from the project's own class distribution, which the statistics panel
 * already fetches. It is a courtesy, not a gate: the server refuses to delete a label that
 * annotations reference, and that refusal is what actually protects the work. Showing the
 * number first means the usual case is not "press delete, read an error".
 */
export function describeLabelUsage(
  labelName: string,
  distribution: Record<string, number>,
): string | null {
  const count = distribution[labelName] ?? 0;
  if (count === 0) return null;
  return count === 1
    ? '1 annotation uses this label'
    : `${count} annotations use this label`;
}
