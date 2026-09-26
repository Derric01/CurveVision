/**
 * Editing a track's keyframes.
 *
 * The timeline already *shows* where a track's keyframes are. These are the four operations
 * that let an annotator act on them, and each one is a pure function from a track to a new
 * track — the write goes through the ordinary autosave path as an `updated_tracks` entry,
 * so nothing here touches the network.
 *
 * One rule dominates all of them:
 *
 * > **Adding or removing a keyframe must never move the object on any other frame.**
 *
 * A track stores a shape at a few frames and interpolates between them. Insert a keyframe
 * carrying the wrong geometry and every frame between it and its neighbours shifts — the
 * annotator sees a box they did not draw, on frames they were not looking at, and the only
 * clue is that their work is subtly wrong later. So `toggleKeyframe` inserts the
 * *interpolated* position, which is by construction the shape already being displayed at
 * that frame, and the insert is a no-op visually.
 *
 * The mirror of that is deletion, which genuinely *does* change the shape between the
 * neighbours — that is what removing a keyframe means. It is offered because an annotator
 * who placed one by mistake needs it, and `__tests__/keyframes.test.ts` pins which frames
 * are allowed to move.
 */

import type { ApiTrack } from '@/api/types';
import type { ShapeType } from '@/canvas/types';
import { interpolateTrack, type InterpolatedShape, type Keyframe } from './interpolate';

/** One stored position on a track, in the shape the API reads and writes. */
export type TrackShape = ApiTrack['shapes'][number];

/** What an edit could not do, and why — for a message rather than a silent no-op. */
export interface Refusal {
  reason: string;
}

export type EditResult =
  | { ok: true; track: ApiTrack }
  /** The track has no keyframes left and should be deleted instead of written. */
  | { ok: true; track: null; deleteTrack: true }
  | { ok: false; refusal: Refusal };

function sorted(shapes: TrackShape[]): TrackShape[] {
  return [...shapes].sort((a, b) => a.frame - b.frame);
}

function asKeyframes(track: ApiTrack): Keyframe[] {
  return sorted(track.shapes).map((shape) => ({
    frame: shape.frame,
    points: shape.points,
    rotation: shape.rotation,
    occluded: shape.occluded,
    outside: shape.outside,
    keyframe: shape.keyframe,
  }));
}

function withShapes(track: ApiTrack, shapes: TrackShape[]): ApiTrack {
  return { ...track, shapes: sorted(shapes) };
}

export function keyframeAt(track: ApiTrack, frame: number): TrackShape | undefined {
  return track.shapes.find((shape) => shape.frame === frame);
}

/**
 * The shape a track is displaying at `frame`, or `null` if it is not on screen there.
 *
 * This is the geometry a new keyframe must carry. Taking the previous keyframe's points
 * instead would be simpler and wrong: on a moving object it would snap the box backwards to
 * where the object used to be.
 */
export function positionAt(track: ApiTrack, frame: number): InterpolatedShape | null {
  return interpolateTrack(asKeyframes(track), track.shape_type as ShapeType, frame);
}

/**
 * Add a keyframe at `frame`, or remove the one already there.
 *
 * Adding uses the interpolated position, so the object does not move. Removing the only
 * remaining keyframe reports `deleteTrack`: a track with no shapes is not a thing the API
 * accepts, and silently leaving an empty one would put a row on the timeline for an object
 * that exists nowhere.
 */
export function toggleKeyframe(track: ApiTrack, frame: number): EditResult {
  const existing = keyframeAt(track, frame);
  if (existing) {
    const remaining = track.shapes.filter((shape) => shape.frame !== frame);
    if (remaining.length === 0) return { ok: true, track: null, deleteTrack: true };
    return { ok: true, track: withShapes(track, remaining) };
  }

  const position = positionAt(track, frame);
  if (!position) {
    return {
      ok: false,
      refusal: {
        reason:
          'The object is not on screen at this frame, so there is no position to record. ' +
          'Draw it here instead, or remove the departure that ends the track before it.',
      },
    };
  }

  return {
    ok: true,
    track: withShapes(track, [
      ...track.shapes,
      {
        frame,
        points: [...position.points],
        rotation: position.rotation,
        occluded: position.occluded,
        outside: false,
        keyframe: true,
        attributes: {},
      } as TrackShape,
    ]),
  };
}

/**
 * Mark the object as leaving at `frame`.
 *
 * An `outside` keyframe is how an annotator says "it goes behind the wall here". It ends the
 * track's presence at that frame; a later keyframe starts it again, which is why a track's
 * presence is a set of intervals rather than one span.
 *
 * The departure carries the position the object was last at, not an empty one, because an
 * exporter that materialises tracks reads those points and a zeroed shape would export a box
 * at the origin.
 *
 * **It also pins the frame before it**, which is not obvious and is the whole reason this
 * function is more than one line. `interpolateTrack` deliberately *holds* the previous
 * position when the next keyframe is a departure, rather than animating towards coordinates
 * that are usually a stale copy. So dropping a departure at frame 6 onto a track with
 * keyframes at 0 and 10 would freeze frames 1-5 at the frame-0 position — the object stops
 * moving on five frames nobody touched. Pinning the already-displayed shape at frame 5 first
 * leaves every earlier frame exactly where it was, and the extra keyframe carries the
 * geometry that was on screen anyway, so it changes nothing by itself.
 */
export function markDeparture(track: ApiTrack, frame: number): EditResult {
  const existing = keyframeAt(track, frame);
  if (existing?.outside) {
    return {
      ok: false,
      refusal: { reason: 'The object already leaves at this frame.' },
    };
  }

  const position = positionAt(track, frame) ?? positionAt(track, frame - 1);
  const points = position ? [...position.points] : [...(sorted(track.shapes)[0]?.points ?? [])];
  const rotation = position?.rotation ?? 0;

  let shapes = track.shapes;
  const previousFrame = frame - 1;
  if (previousFrame >= 0 && !keyframeAt(track, previousFrame)) {
    const held = positionAt(track, previousFrame);
    if (held) {
      shapes = [
        ...shapes,
        {
          frame: previousFrame,
          points: [...held.points],
          rotation: held.rotation,
          occluded: held.occluded,
          outside: false,
          keyframe: true,
          attributes: {},
        } as TrackShape,
      ];
    }
  }

  const others = shapes.filter((shape) => shape.frame !== frame);
  const departure = {
    frame,
    points,
    rotation,
    occluded: existing?.occluded ?? position?.occluded ?? false,
    outside: true,
    keyframe: true,
    attributes: existing?.attributes ?? {},
  } as TrackShape;

  // A departure on the very first frame would define a track that is never on screen.
  if (others.length === 0) {
    return {
      ok: false,
      refusal: {
        reason:
          'A track whose only keyframe is a departure is never on screen. ' +
          'Add a position first, then mark where the object leaves.',
      },
    };
  }
  return { ok: true, track: withShapes(track, [...others, departure]) };
}

/** Remove the keyframe at `frame`, if there is one. */
export function removeKeyframe(track: ApiTrack, frame: number): EditResult {
  if (!keyframeAt(track, frame)) {
    return { ok: false, refusal: { reason: 'There is no keyframe on this frame.' } };
  }
  return toggleKeyframe(track, frame);
}

/**
 * Move the keyframe at `from` to `to`, keeping its geometry.
 *
 * Refused when `to` already holds a keyframe: the API rejects a track with two shapes on one
 * frame, and silently merging them would discard one of the annotator's positions without
 * saying so.
 */
export function moveKeyframe(track: ApiTrack, from: number, to: number): EditResult {
  if (from === to) return { ok: true, track };
  const moving = keyframeAt(track, from);
  if (!moving) {
    return { ok: false, refusal: { reason: 'There is no keyframe on this frame to move.' } };
  }
  if (keyframeAt(track, to)) {
    return {
      ok: false,
      refusal: { reason: `Frame ${to} already has a keyframe. Remove it first.` },
    };
  }
  if (to < 0) {
    return { ok: false, refusal: { reason: 'A keyframe cannot move before frame 0.' } };
  }

  const others = track.shapes.filter((shape) => shape.frame !== from);
  return { ok: true, track: withShapes(track, [...others, { ...moving, frame: to }]) };
}

/** Where an object was put on one frame: what a drag, a vertex edit or an occlusion toggle changes. */
export interface Placement {
  points: number[];
  rotation: number;
  occluded: boolean;
}

/**
 * Record where a tracked object was put on `frame`, as a keyframe there.
 *
 * This is what editing a tracked object on the canvas means: the frame being edited becomes
 * a keyframe carrying the new geometry, and the frames between it and its neighbours now
 * interpolate towards it — which is the point, since the object moved. CVAT does the same
 * (`Track.savePoints` in `cvat-core`): a frame that was only interpolated is copied into a
 * stored shape with the new points.
 *
 * A keyframe already on that frame keeps its per-frame attributes; only its geometry
 * changes, and it stops being a departure, because an object somebody just moved on this
 * frame is visibly here.
 *
 * `null` when the placement is exactly what the track already shows at that frame. An
 * undo hands back every object on the frame, moved or not, and writing a keyframe for each
 * of them would litter every track on screen with keyframes that change nothing.
 */
export function placeKeyframe(track: ApiTrack, frame: number, placement: Placement): ApiTrack | null {
  const shown = positionAt(track, frame);
  if (
    shown &&
    shown.rotation === placement.rotation &&
    shown.occluded === placement.occluded &&
    samePoints(shown.points, placement.points)
  ) {
    return null;
  }

  const existing = keyframeAt(track, frame);
  const others = track.shapes.filter((shape) => shape.frame !== frame);
  return withShapes(track, [
    ...others,
    {
      frame,
      z_order: existing?.z_order ?? 0,
      attributes: existing?.attributes ?? {},
      points: [...placement.points],
      rotation: placement.rotation,
      occluded: placement.occluded,
      outside: false,
      keyframe: true,
    },
  ]);
}

/** One tracked object as the canvas left it on one frame. */
export interface CanvasEdit extends Placement {
  trackId: string;
  frame: number;
  labelId: string;
}

/**
 * The tracks a batch of canvas edits actually changes, with the edits applied.
 *
 * Each edit becomes a keyframe through `placeKeyframe`, in frame order, so a track moved on
 * two frames before a save gets both. A relabel applies to the whole track — a track is one
 * object, and "this car is a van on frame 7 only" is not something the model can hold.
 * Tracks the batch leaves unchanged are not returned, so they are not rewritten; nor is a
 * track the document no longer has, since there is nothing left to put a keyframe on.
 */
export function applyCanvasEdits(
  tracks: readonly ApiTrack[],
  edits: readonly CanvasEdit[],
): ApiTrack[] {
  const byId = new Map(tracks.map((track) => [track.id, track]));
  const changed = new Map<string, ApiTrack>();
  for (const edit of [...edits].sort((a, b) => a.frame - b.frame)) {
    const track = changed.get(edit.trackId) ?? byId.get(edit.trackId);
    if (!track) continue;
    const relabelled = track.label_id === edit.labelId ? track : { ...track, label_id: edit.labelId };
    const placed = placeKeyframe(relabelled, edit.frame, edit);
    if (placed) changed.set(track.id, placed);
    else if (relabelled !== track) changed.set(track.id, relabelled);
  }
  return [...changed.values()];
}

function samePoints(a: readonly number[], b: readonly number[]): boolean {
  return a.length === b.length && a.every((value, index) => Math.abs(value - (b[index] ?? 0)) < 1e-6);
}

/**
 * Every frame in `[start, stop]` where the track's displayed shape differs from `before`.
 *
 * Used by the tests rather than by the editor: the claim "adding a keyframe moves nothing"
 * is only worth making if something checks it frame by frame.
 */
export function framesThatMoved(
  before: ApiTrack,
  after: ApiTrack,
  start: number,
  stop: number,
): number[] {
  const moved: number[] = [];
  const shapeType = before.shape_type as ShapeType;
  const left = asKeyframes(before);
  const right = asKeyframes(after);
  for (let frame = start; frame <= stop; frame++) {
    const a = interpolateTrack(left, shapeType, frame);
    const b = interpolateTrack(right, shapeType, frame);
    if (!a && !b) continue;
    if (!a || !b) {
      moved.push(frame);
      continue;
    }
    if (!samePoints(a.points, b.points)) moved.push(frame);
  }
  return moved;
}
