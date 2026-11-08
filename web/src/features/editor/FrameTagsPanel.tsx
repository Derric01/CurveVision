/**
 * The current frame's tags: what is said of the whole frame, with a way to add one and to
 * take one off. Helpers in `frameTags.ts`; the write goes through autosave's turn
 * (`useAutosave.writeTags`) so it never races a save of the shapes on the same job.
 */

import { useState } from 'react';
import { Plus, X } from 'lucide-react';
import type { LabelStyle } from '@/canvas/types';
import { ErrorNotice } from '@/ui/primitives';
import { taggableLabels, type FrameTag } from './frameTags';

export function FrameTagsPanel({
  tags,
  labels,
  currentFrame,
  onWrite,
}: {
  tags: readonly FrameTag[];
  labels: readonly LabelStyle[];
  currentFrame: number;
  onWrite: (change: {
    created?: { label_id: string; frame: number }[];
    deletedIds?: string[];
  }) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const offered = taggableLabels(labels, tags);

  const write = async (change: Parameters<typeof onWrite>[0]) => {
    setBusy(true);
    setError(null);
    try {
      await onWrite(change);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border-t border-ink-800 px-3 py-2" data-frame-tags="">
      <h3 className="py-1 text-xs font-medium uppercase tracking-wide text-ink-500">
        Frame tags
      </h3>
      {tags.length === 0 ? (
        <p className="text-[11px] text-ink-600" data-frame-tags-empty="">
          Nothing said of frame {currentFrame + 1} as a whole.
        </p>
      ) : (
        <ul className="flex flex-wrap gap-1">
          {tags.map((tag) => (
            <li
              key={tag.id}
              className="flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] text-ink-100"
              style={{ borderColor: tag.color }}
              data-frame-tag={tag.name}
              title={tag.suggested ? 'A model’s suggestion, not yet accepted' : undefined}
            >
              <span className="h-2 w-2 rounded-full" style={{ background: tag.color }} />
              {tag.name}
              {tag.suggested && <span className="text-ink-500">?</span>}
              <button
                type="button"
                className="ml-0.5 text-ink-500 hover:text-ink-100 disabled:opacity-40"
                onClick={() => void write({ deletedIds: [tag.id] })}
                disabled={busy}
                aria-label={`Remove the tag ${tag.name}`}
                data-frame-tag-remove={tag.name}
              >
                <X size={11} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {offered.length > 0 && (
        <label className="mt-2 flex items-center gap-1.5 text-[11px] text-ink-400">
          <Plus size={12} />
          <select
            className="min-w-0 flex-1 rounded border border-ink-700 bg-ink-950 px-1.5 py-0.5 text-ink-200"
            value=""
            disabled={busy}
            onChange={(event) => {
              const labelId = event.target.value;
              if (labelId) void write({ created: [{ label_id: labelId, frame: currentFrame }] });
            }}
            aria-label="Tag this frame"
            data-frame-tag-add=""
          >
            <option value="">Tag this frame…</option>
            {offered.map((label) => (
              <option key={label.id} value={label.id}>
                {label.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {error != null && (
        <div className="mt-2">
          <ErrorNotice error={error} />
        </div>
      )}
    </div>
  );
}
