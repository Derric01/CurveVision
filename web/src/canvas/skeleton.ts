/**
 * Skeletons: placing a fixed set of named joints, in a fixed order.
 *
 * A skeleton is not a free point set. Its label declares one child label per joint, and
 * **that order is the contract** — `yolo_pose` writes `px py v` triples positionally, so a
 * shape whose elements arrive in a different order, or which omits a joint the photographer
 * could not see, shifts every later value into the wrong joint. A dataset wrong in that way
 * looks fine and trains a model to put elbows where wrists are.
 *
 * So a skipped joint is *recorded* rather than dropped: it becomes an element with
 * `outside: true`, which is Ultralytics' visibility 0. Every skeleton of a class therefore
 * has the same number of elements in the same order, whatever the annotator could see.
 *
 * Everything here is pure. The tool in `tools.ts` is a thin state machine over it, and the
 * renderer reads `bones` — which means the ordering rules can be tested without a canvas.
 */

import type { Point, SkeletonElement } from './types';

export interface JointSlot {
  /** The child label this joint is annotated with. */
  labelId: string;
  name: string;
}

export interface SkeletonSchema {
  /** The parent (skeleton) label. */
  labelId: string;
  name: string;
  /** Joints in the order the label declares them. Never reordered. */
  joints: JointSlot[];
  /** Bones, as index pairs into `joints`. */
  edges: readonly (readonly [number, number])[];
}

/** A joint the annotator has placed. `null` in a placement means "skipped, not visible". */
export interface PlacedJoint {
  point: Point;
  occluded: boolean;
}

/**
 * Progress through one skeleton: one entry per joint already decided, in joint order.
 *
 * Its length *is* the cursor — `placement.length` is the index of the joint being asked for
 * next. A `null` entry is a joint the annotator explicitly skipped, which is different from
 * a joint not yet reached and has to stay different: the first becomes a zero-visibility
 * element, the second has not happened yet.
 */
export type Placement = readonly (PlacedJoint | null)[];

/** Where a skipped joint is written. Ultralytics pads with zeros; so do we. */
const ABSENT: readonly [number, number] = [0, 0];

/**
 * The schema for a label, or `null` when that label is not a skeleton.
 *
 * A label with children but no edges is still a skeleton — plenty of keypoint datasets
 * declare no connectivity at all, and refusing to draw one because nobody listed the bones
 * would be inventing a requirement.
 */
export function skeletonSchema(label: {
  id: string;
  name: string;
  children?: readonly { id: string; name: string }[];
  skeletonEdges?: readonly (readonly number[])[];
}): SkeletonSchema | null {
  const children = label.children ?? [];
  if (children.length === 0) return null;

  const joints = children.map((child) => ({ labelId: child.id, name: child.name }));
  const edges: [number, number][] = [];
  for (const edge of label.skeletonEdges ?? []) {
    const [from, to] = edge;
    // An edge naming a joint that does not exist is schema damage, not something to draw a
    // line for. Dropping it keeps a bad edge from moving every later bone.
    if (from == null || to == null) continue;
    if (!Number.isInteger(from) || !Number.isInteger(to)) continue;
    if (from < 0 || to < 0 || from >= joints.length || to >= joints.length) continue;
    if (from === to) continue;
    edges.push([from, to]);
  }

  return { labelId: label.id, name: label.name, joints, edges };
}

/** The joint being asked for next, or `null` once every joint has been decided. */
export function nextJoint(schema: SkeletonSchema, placement: Placement): JointSlot | null {
  return schema.joints[placement.length] ?? null;
}

export function isComplete(schema: SkeletonSchema, placement: Placement): boolean {
  return placement.length >= schema.joints.length;
}

/**
 * Whether this placement is worth committing.
 *
 * At least one joint has to be somewhere. A skeleton of nothing but skipped joints is a
 * row of zeroes that claims an object is present and tells a model nothing about it.
 */
export function canCommit(placement: Placement): boolean {
  return placement.some((joint) => joint !== null);
}

/** How many joints are actually placed, as opposed to skipped. */
export function placedCount(placement: Placement): number {
  return placement.reduce((total, joint) => (joint === null ? total : total + 1), 0);
}

/**
 * What to tell the annotator right now.
 *
 * Counts from 1 and says the total, because "place the left wrist" on its own gives no
 * sense of how much is left in a seventeen-joint pose.
 */
export function describeProgress(schema: SkeletonSchema, placement: Placement): string {
  const joint = nextJoint(schema, placement);
  const total = schema.joints.length;
  if (joint === null) {
    const placed = placedCount(placement);
    if (placed === total) return `All ${total} joints placed — press Enter to finish`;
    return `${placed} of ${total} joints placed, ${total - placed} skipped — Enter to finish`;
  }
  return `${joint.name} — joint ${placement.length + 1} of ${total}`;
}

/**
 * The committed elements: one per declared joint, in declared order, always.
 *
 * Joints beyond the placement are treated as skipped rather than omitted, so committing an
 * unfinished skeleton still produces a full-width row. `yolo_pose` slices positionally; a
 * short list would shift every later joint.
 */
export function buildElements(schema: SkeletonSchema, placement: Placement): SkeletonElement[] {
  return schema.joints.map((joint, index) => {
    const placed = placement[index] ?? null;
    if (!placed) {
      return { labelId: joint.labelId, points: [...ABSENT], occluded: false, outside: true };
    }
    return {
      labelId: joint.labelId,
      points: [placed.point.x, placed.point.y],
      occluded: placed.occluded,
      outside: false,
    };
  });
}

/**
 * The parent shape's own `points`: every joint that exists, flattened.
 *
 * The server lets a skeleton's `points` be empty — the elements are the geometry — but the
 * editor computes bounds, hit-testing and the label chip from `points`, so a skeleton with
 * none of them would be selectable nowhere and labelled off-screen. Skipped joints are left
 * out: including their `(0, 0)` would stretch every skeleton's bounding box to the top-left
 * corner of the image.
 */
export function skeletonPoints(elements: readonly SkeletonElement[]): number[] {
  const points: number[] = [];
  for (const element of elements) {
    if (element.outside) continue;
    const [x, y] = element.points;
    if (x == null || y == null) continue;
    points.push(x, y);
  }
  return points;
}

/** A joint as the renderer wants it: where it is, and how sure the annotator was. */
export interface DrawableJoint {
  index: number;
  point: Point;
  occluded: boolean;
}

/** Every joint that has a position, with its index kept so bones can find it. */
export function drawableJoints(elements: readonly SkeletonElement[]): DrawableJoint[] {
  const joints: DrawableJoint[] = [];
  elements.forEach((element, index) => {
    if (element.outside) return;
    const [x, y] = element.points;
    if (x == null || y == null) return;
    joints.push({ index, point: { x, y }, occluded: element.occluded });
  });
  return joints;
}

/**
 * The bones to draw, in image space.
 *
 * A bone with an endpoint that was skipped is not drawn — guessing where a hidden joint
 * would have been is the one thing a keypoint tool must never do, because the guess is
 * indistinguishable on screen from an annotation somebody made.
 */
export function bones(
  edges: readonly (readonly [number, number])[],
  elements: readonly SkeletonElement[],
): [Point, Point][] {
  const at = new Map(drawableJoints(elements).map((joint) => [joint.index, joint.point]));
  const segments: [Point, Point][] = [];
  for (const [from, to] of edges) {
    const start = at.get(from);
    const end = at.get(to);
    if (start && end) segments.push([start, end]);
  }
  return segments;
}
