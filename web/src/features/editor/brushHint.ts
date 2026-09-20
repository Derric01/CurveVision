/**
 * What to tell somebody who has picked up the brush.
 *
 * Unlike the skeleton tool, drawing with the brush is never blocked on a schema decision —
 * there is always something to paint into, and clicking never silently does nothing. So this
 * is not three states to reason through, only two lines: what the tool does while nothing is
 * happening yet, and `BrushTool`'s own status once a stroke tells it what it is doing (a new
 * mask or an edit to the selected one, at what size). Still pulled out as a pure function
 * rather than written inline, because the wording is worth testing without a DOM and because
 * that is the pattern every other tool hint in this editor already follows.
 */

const IDLE_HINT = 'Paint a mask. Right-click or hold Alt to erase · [ / ] resizes the brush.';

/**
 * The line shown above the canvas while the brush is active.
 *
 * `status` is what `BrushTool` last reported — set once a stroke begins or the size
 * changes, and left as whatever the *previous* tool last set until then, since switching
 * tools does not itself clear it. Passing `null` explicitly on every tool switch, rather
 * than trusting this function to guess whether a stale line belongs to it, is the caller's
 * job; this only decides what to show once that is done correctly.
 */
export function brushHint(status: string | null): string {
  return status ?? IDLE_HINT;
}
