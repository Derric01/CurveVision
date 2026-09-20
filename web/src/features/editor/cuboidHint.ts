/**
 * What to tell somebody who has picked up the cuboid tool.
 *
 * Drawing one is two gestures, not one -- a drag for the front face, then a click for the
 * depth -- and unlike a rectangle's single drag, an annotator has no way to see from the
 * canvas alone which of the two they are in. `CuboidTool` reports that through the same
 * `status` field the skeleton and brush tools already use; this only supplies what to show
 * before the first drag has started, when the tool has not set a status of its own yet.
 */

const IDLE_HINT = 'Drag to draw the front face, then move and click to set the depth.';

/** The line shown above the canvas while the cuboid tool is active. */
export function cuboidHint(status: string | null): string {
  return status ?? IDLE_HINT;
}
