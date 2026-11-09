/**
 * The selected object's own controls: its label, whether it is occluded, and where it sits
 * in the stacking order.
 *
 * The data model and the engine could do all three, and nothing on screen could: changing
 * a label meant deleting the object and drawing it again. Each control goes through the
 * engine, so it is undoable, one step per change, and reaches autosave like a drag does.
 * Shown for a selection of one or more objects; the label list shows only labels that
 * can be drawn.
 */

import { ArrowDownToLine, ArrowUpToLine } from 'lucide-react';
import type { LabelStyle } from '@/canvas/types';

export function ObjectPanel({
  count,
  labelId,
  occluded,
  labels,
  onRelabel,
  onToggleOccluded,
  onRestack,
}: {
  /** How many objects are selected. */
  count: number;
  /** Their label, when they share one. */
  labelId: string | null;
  /** Whether every one of them is occluded. */
  occluded: boolean;
  labels: LabelStyle[];
  onRelabel: (labelId: string) => void;
  onToggleOccluded: () => void;
  onRestack: (where: 'front' | 'back') => void;
}) {
  if (count === 0) return null;
  return (
    <section className="border-t border-ink-800 px-3 py-2" data-object-panel="">
      <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-ink-500">
        {count === 1 ? 'Selected object' : `${count} selected objects`}
      </h3>
      <div className="space-y-2 text-xs">
        <label className="flex items-center justify-between gap-2 text-ink-300">
          <span>Label</span>
          <select
            className="min-w-0 flex-1 rounded border border-ink-700 bg-ink-900 px-1.5 py-1 text-ink-100"
            value={labelId ?? ''}
            onChange={(event) => {
              if (event.target.value) onRelabel(event.target.value);
            }}
            data-relabel=""
          >
            {labelId === null && <option value="">Mixed</option>}
            {labels.map((label) => (
              <option key={label.id} value={label.id}>
                {label.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-ink-300" title="Q">
          <input
            type="checkbox"
            checked={occluded}
            onChange={onToggleOccluded}
            data-occluded=""
          />
          Occluded <span className="text-ink-600">(Q)</span>
        </label>
        <div className="flex gap-2">
          <button
            type="button"
            className="flex flex-1 items-center justify-center gap-1 rounded border border-ink-700 px-2 py-1 text-ink-300 hover:bg-ink-850"
            onClick={() => onRestack('front')}
            title="Bring to front (])"
            data-restack="front"
          >
            <ArrowUpToLine size={12} /> Front
          </button>
          <button
            type="button"
            className="flex flex-1 items-center justify-center gap-1 rounded border border-ink-700 px-2 py-1 text-ink-300 hover:bg-ink-850"
            onClick={() => onRestack('back')}
            title="Send to back ([)"
            data-restack="back"
          >
            <ArrowDownToLine size={12} /> Back
          </button>
        </div>
      </div>
    </section>
  );
}
