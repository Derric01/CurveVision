/**
 * Review issues, in the editor.
 *
 * An issue is how a reviewer sends work back — a comment thread anchored to a frame and
 * optionally to the object being complained about. The model, the API and the permissions
 * have existed since the first iteration; nothing called them, so receiving review feedback
 * meant reading it out of the database.
 *
 * The shape mirrors the quality panel deliberately: both are frame-anchored lists a reviewer
 * clicks through, and clicking a row seeks to the frame it is about. A reviewer moving
 * between "what did the comparison find" and "what did a person say" should not have to
 * learn two interfaces.
 *
 * What it is careful about:
 *
 * - **Opening an issue anchors it to the selected object when there is one.** "This box is
 *   wrong" is the most common review comment there is, and the editor already knows which
 *   box. A track anchors by `track_id` and a shape by `shape_id`; see `anchorFor`.
 * - **Resolved issues stay visible**, collapsed. Hiding them makes a thread that was
 *   resolved by mistake unrecoverable from the UI.
 * - **It does not draw a pin on the canvas.** `position` is stored and the API accepts it,
 *   but placing one needs a click-to-place interaction that is not built; issues opened here
 *   carry a frame and an object rather than a point, which is honest about what they have.
 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, ChevronRight, MapPin, MessageSquare, RotateCcw, Send, X } from 'lucide-react';
import clsx from 'clsx';

import { api } from '@/api/client';
import type { Annotation } from '@/canvas/types';
import { Badge, Button, Spinner } from '@/ui/primitives';
import { anchorFor, describeAnchor, issueRows, openCount, type IssueRow } from './issues';

export function IssuesPanel({
  jobId,
  currentFrame,
  selected,
  onSeek,
  picking,
  pickedPoint,
  onPickingChange,
  onClearPoint,
  onOpenThread,
}: {
  jobId: string;
  currentFrame: number;
  /** The annotation an issue would be anchored to, when one is selected. */
  selected: Annotation | null;
  onSeek: (frame: number) => void;
  /** True while the next canvas click will place a pin. */
  picking: boolean;
  /** The point the last click placed, in image space, or null. */
  pickedPoint: { x: number; y: number } | null;
  onPickingChange: (picking: boolean) => void;
  onClearPoint: () => void;
  /** The thread being read, so the canvas can draw its pin brighter. */
  onOpenThread: (issueId: string | null) => void;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState('');
  const [openThread, setOpenThread] = useState<string | null>(null);
  const [reply, setReply] = useState('');
  const [showResolved, setShowResolved] = useState(false);

  const issues = useQuery({
    queryKey: ['issues', jobId],
    queryFn: () => api.issues(jobId),
    enabled: Boolean(jobId),
    retry: false,
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['issues', jobId] });

  const create = useMutation({
    mutationFn: () =>
      api.createIssue(jobId, {
        frame: currentFrame,
        body: draft.trim(),
        ...anchorFor(selected),
        // Sent only when a point was actually placed. The API defaults `position` to an
        // empty list, which is what "not pinned" means; sending `[0, 0]` for an unplaced
        // pin would put a marker in the top-left corner of every such issue.
        ...(pickedPoint ? { position: [pickedPoint.x, pickedPoint.y] } : {}),
      }),
    onSuccess: async () => {
      setDraft('');
      onClearPoint();
      await refresh();
    },
  });

  const comment = useMutation({
    mutationFn: (input: { issueId: string; body: string }) =>
      api.addComment(jobId, input.issueId, input.body),
    onSuccess: async () => {
      setReply('');
      await refresh();
    },
  });

  const setState = useMutation({
    mutationFn: (input: { issueId: string; state: 'open' | 'resolved' }) =>
      api.setIssueState(jobId, input.issueId, input.state),
    onSuccess: () => refresh(),
  });

  const lists = issueRows(issues.data, currentFrame);
  const outstanding = openCount(issues.data);
  const error = create.error ?? comment.error ?? setState.error ?? issues.error;

  return (
    <div className="flex shrink-0 flex-col border-t border-ink-800">
      <h3 className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium uppercase tracking-wide text-ink-500">
        <MessageSquare size={12} />
        Issues
        {outstanding > 0 && (
          <Badge tone="warning" className="ml-auto">
            {outstanding} open
          </Badge>
        )}
      </h3>

      <div className="px-3 pb-3">
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          rows={2}
          placeholder={
            selected
              ? `Something wrong with the selected object on frame ${currentFrame}?`
              : `Something wrong on frame ${currentFrame}?`
          }
          className="w-full resize-none rounded-md border border-ink-700 bg-ink-950 px-2 py-1.5 text-xs text-ink-100 placeholder:text-ink-600 focus:border-curve-400 focus:outline-none"
          aria-label="Describe the problem"
        />
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => create.mutate()}
            disabled={draft.trim().length === 0 || create.isPending}
          >
            {create.isPending ? <Spinner className="mr-1.5 h-3 w-3" /> : <Send size={12} className="mr-1.5" />}
            Open an issue
          </Button>
          <Button
            size="sm"
            variant="ghost"
            active={picking}
            onClick={() => onPickingChange(!picking)}
            title={picking ? 'Click the image to place the pin' : 'Point at the problem'}
          >
            <MapPin size={12} className="mr-1" />
            {picking ? 'Click the image' : 'Pin'}
          </Button>
          {pickedPoint && (
            <Button size="sm" variant="ghost" onClick={onClearPoint} title="Remove the pin">
              <X size={11} />
            </Button>
          )}
        </div>
        <p className="mt-1 truncate text-[10px] text-ink-600">
          {/* Says what it will attach to before it attaches it — an issue that silently lost
              its anchor reads exactly like one that never had it. */}
          {pickedPoint
            ? `pinned at ${Math.round(pickedPoint.x)}, ${Math.round(pickedPoint.y)}`
            : selected
              ? 'on the selected object'
              : `on frame ${currentFrame}`}
        </p>

        {error !== null && error !== undefined && (
          <p className="mt-2 rounded border border-red-500/30 bg-red-500/10 px-2 py-1.5 text-[11px] text-red-300">
            {error instanceof Error ? error.message : 'That did not work'}
          </p>
        )}
      </div>

      {issues.isLoading ? (
        <div className="flex items-center gap-2 px-3 pb-3 text-xs text-ink-500">
          <Spinner className="h-3 w-3" /> Loading
        </div>
      ) : (
        <ul className="max-h-[26vh] overflow-auto border-t border-ink-800">
          {lists.open.length === 0 && (
            <li className="px-3 py-3 text-xs text-ink-600">Nothing open on this job.</li>
          )}
          {lists.open.map((row) => (
            <IssueItem
              key={row.issue.id}
              row={row}
              expanded={openThread === row.issue.id}
              reply={reply}
              busy={comment.isPending || setState.isPending}
              onToggle={() => {
                const next = openThread === row.issue.id ? null : row.issue.id;
                setOpenThread(next);
                onOpenThread(next);
                setReply('');
              }}
              onSeek={() => onSeek(row.issue.frame)}
              onReplyChange={setReply}
              onReply={() =>
                comment.mutate({ issueId: row.issue.id, body: reply.trim() })
              }
              onSetState={(state) => setState.mutate({ issueId: row.issue.id, state })}
            />
          ))}

          {lists.resolved.length > 0 && (
            <li className="border-t border-ink-800">
              <button
                type="button"
                onClick={() => setShowResolved((current) => !current)}
                className="flex w-full items-center gap-1.5 px-3 py-1.5 text-left text-[11px] text-ink-500 hover:text-ink-300"
                aria-expanded={showResolved}
              >
                <ChevronRight
                  size={11}
                  className={clsx('transition-transform', showResolved && 'rotate-90')}
                />
                {lists.resolved.length} resolved
              </button>
            </li>
          )}
          {showResolved &&
            lists.resolved.map((row) => (
              <IssueItem
                key={row.issue.id}
                row={row}
                expanded={openThread === row.issue.id}
                reply={reply}
                busy={comment.isPending || setState.isPending}
                onToggle={() => {
                  const next = openThread === row.issue.id ? null : row.issue.id;
                  setOpenThread(next);
                  onOpenThread(next);
                  setReply('');
                }}
                onSeek={() => onSeek(row.issue.frame)}
                onReplyChange={setReply}
                onReply={() => comment.mutate({ issueId: row.issue.id, body: reply.trim() })}
                onSetState={(state) => setState.mutate({ issueId: row.issue.id, state })}
              />
            ))}
        </ul>
      )}
    </div>
  );
}

function IssueItem({
  row,
  expanded,
  reply,
  busy,
  onToggle,
  onSeek,
  onReplyChange,
  onReply,
  onSetState,
}: {
  row: IssueRow;
  expanded: boolean;
  reply: string;
  busy: boolean;
  onToggle: () => void;
  onSeek: () => void;
  onReplyChange: (value: string) => void;
  onReply: () => void;
  onSetState: (state: 'open' | 'resolved') => void;
}) {
  const resolved = row.issue.state !== 'open';
  return (
    <li className={clsx('border-b border-ink-800/60', resolved && 'opacity-60')}>
      <div className="flex items-start gap-1">
        <button
          type="button"
          onClick={onToggle}
          className="min-w-0 flex-1 px-3 py-1.5 text-left"
          aria-expanded={expanded}
        >
          <p className={clsx('truncate text-[11px]', resolved ? 'text-ink-400' : 'text-ink-200')}>
            {row.preview || '(no description)'}
          </p>
          <p className="mt-0.5 flex items-center gap-1.5 text-[10px] text-ink-600">
            <span className={clsx(row.onCurrentFrame && 'text-curve-300')}>
              {describeAnchor(row.issue)}
            </span>
            {row.author && <span>· {row.author}</span>}
            {row.replies > 0 && (
              <span>
                · {row.replies} repl{row.replies === 1 ? 'y' : 'ies'}
              </span>
            )}
          </p>
        </button>
        <button
          type="button"
          onClick={onSeek}
          title={`Go to frame ${row.issue.frame}`}
          className="mt-1.5 shrink-0 px-1 text-ink-600 hover:text-ink-200"
        >
          <ChevronRight size={12} />
        </button>
      </div>

      {expanded && (
        <div className="px-3 pb-2">
          <ul className="space-y-1.5 border-l border-ink-800 pl-2">
            {(row.issue.comments ?? []).map((entry) => (
              <li key={entry.id} className="text-[11px]">
                <p className="text-ink-300">{entry.body}</p>
                <p className="text-[10px] text-ink-600">{entry.author?.username ?? 'someone'}</p>
              </li>
            ))}
          </ul>

          <textarea
            value={reply}
            onChange={(event) => onReplyChange(event.target.value)}
            rows={2}
            placeholder="Reply"
            className="mt-2 w-full resize-none rounded-md border border-ink-700 bg-ink-950 px-2 py-1.5 text-[11px] text-ink-100 placeholder:text-ink-600 focus:border-curve-400 focus:outline-none"
            aria-label="Reply to this issue"
          />
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <Button
              size="sm"
              variant="ghost"
              onClick={onReply}
              disabled={reply.trim().length === 0 || busy}
            >
              <Send size={11} className="mr-1" />
              Reply
            </Button>
            {resolved ? (
              <Button size="sm" variant="ghost" onClick={() => onSetState('open')} disabled={busy}>
                <RotateCcw size={11} className="mr-1" />
                Reopen
              </Button>
            ) : (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onSetState('resolved')}
                disabled={busy}
              >
                <Check size={11} className="mr-1" />
                Resolve
              </Button>
            )}
          </div>
        </div>
      )}
    </li>
  );
}
