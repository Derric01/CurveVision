/**
 * Reading a job's issues.
 *
 * The test doing the most work here is `anchorFor`. A track materialised onto a frame is not
 * a shape and has no row in the shapes table, but the editor gives it an annotation id all
 * the same — the track's. Sending that as `shape_id` sets a foreign key to a row that does
 * not exist in that table, and the anchor is silently dropped: the issue still saves, still
 * lists, and just quietly stops pointing at anything. Nothing in the UI would show it.
 */

import { describe, expect, it } from 'vitest';

import type { Annotation } from '@/canvas/types';
import type { Issue } from '@/api/types';
import { anchorFor, describeAnchor, hasPin, issuePins, issueRows, openCount } from '../issues';

function comment(body: string, author: string | null = 'rey', at = '2026-09-13T00:00:00Z') {
  return {
    id: `c-${body}`,
    body,
    created_at: at,
    author: author ? { id: 'u1', username: author, full_name: null, avatar_url: null } : null,
  };
}

function issue(overrides: Partial<Issue> = {}): Issue {
  return {
    id: 'issue-1',
    job_id: 'job-1',
    frame: 0,
    position: [],
    shape_id: null,
    track_id: null,
    state: 'open',
    created_at: '2026-09-13T00:00:00Z',
    resolved_at: null,
    comments: [comment('the box is too loose')],
    ...overrides,
  } as Issue;
}

function annotation(overrides: Partial<Annotation> = {}): Annotation {
  return {
    id: 'shape-1',
    labelId: 'label-1',
    frame: 0,
    shapeType: 'rectangle',
    points: [0, 0, 10, 10],
    rotation: 0,
    occluded: false,
    outside: false,
    zOrder: 0,
    group: null,
    source: 'manual',
    confidence: null,
    attributes: {},
    ...overrides,
  } as Annotation;
}

describe('issueRows', () => {
  it('separates work from history', () => {
    const lists = issueRows(
      [issue({ id: 'a' }), issue({ id: 'b', state: 'resolved', resolved_at: '2026-09-13T01:00:00Z' })],
      0,
    );
    expect(lists.open.map((row) => row.issue.id)).toEqual(['a']);
    expect(lists.resolved.map((row) => row.issue.id)).toEqual(['b']);
  });

  it('walks open issues forwards through the video', () => {
    const lists = issueRows(
      [issue({ id: 'late', frame: 40 }), issue({ id: 'early', frame: 2 })],
      0,
    );
    expect(lists.open.map((row) => row.issue.id)).toEqual(['early', 'late']);
  });

  it('breaks a same-frame tie by when it was opened, so the order is stable', () => {
    const lists = issueRows(
      [
        issue({ id: 'second', frame: 5, created_at: '2026-09-13T02:00:00Z' }),
        issue({ id: 'first', frame: 5, created_at: '2026-09-13T01:00:00Z' }),
      ],
      0,
    );
    expect(lists.open.map((row) => row.issue.id)).toEqual(['first', 'second']);
  });

  it('reads resolved issues backwards, newest first', () => {
    // The one resolved a minute ago is the one you might reopen.
    const lists = issueRows(
      [
        issue({ id: 'old', state: 'resolved', resolved_at: '2026-09-01T00:00:00Z' }),
        issue({ id: 'recent', state: 'resolved', resolved_at: '2026-09-12T00:00:00Z' }),
      ],
      0,
    );
    expect(lists.resolved.map((row) => row.issue.id)).toEqual(['recent', 'old']);
  });

  it('keeps a resolved issue with no resolved_at rather than dropping it', () => {
    const lists = issueRows([issue({ id: 'x', state: 'resolved', resolved_at: null })], 0);
    expect(lists.resolved.map((row) => row.issue.id)).toEqual(['x']);
  });

  it('previews what the issue is, not what was said last', () => {
    // Retitling a thread on every reply would make the list say "ok, thanks".
    const row = issueRows(
      [
        issue({
          comments: [
            comment('the box is too loose', 'rey', '2026-09-13T00:00:00Z'),
            comment('ok, thanks', 'finn', '2026-09-13T05:00:00Z'),
          ],
        }),
      ],
      0,
    ).open[0];
    expect(row?.preview).toBe('the box is too loose');
    expect(row?.author).toBe('rey');
    expect(row?.replies).toBe(1);
  });

  it('counts no replies on a thread of one', () => {
    expect(issueRows([issue()], 0).open[0]?.replies).toBe(0);
  });

  it('marks the issue whose frame is on screen', () => {
    const lists = issueRows([issue({ id: 'here', frame: 7 }), issue({ id: 'away', frame: 9 })], 7);
    expect(lists.open.find((row) => row.issue.id === 'here')?.onCurrentFrame).toBe(true);
    expect(lists.open.find((row) => row.issue.id === 'away')?.onCurrentFrame).toBe(false);
  });

  it('survives an issue with no comments and no issues at all', () => {
    expect(issueRows([issue({ comments: [] })], 0).open[0]?.preview).toBe('');
    expect(issueRows(undefined, 0)).toEqual({ open: [], resolved: [] });
  });
});

describe('openCount', () => {
  it('counts only what is outstanding', () => {
    expect(openCount([issue(), issue({ state: 'resolved' }), issue()])).toBe(2);
    expect(openCount(undefined)).toBe(0);
  });
});

describe('anchorFor', () => {
  it('anchors a plain shape by its shape id', () => {
    expect(anchorFor(annotation({ id: 'shape-9' }))).toEqual({ shape_id: 'shape-9' });
  });

  it('anchors a track position by its TRACK id, never as a shape', () => {
    // A materialised track position has no row in the shapes table. Sending its id as
    // `shape_id` points a foreign key at nothing: the issue saves, lists, and silently
    // stops pointing at the object it was about.
    const anchor = anchorFor(annotation({ id: 'track-3', trackId: 'track-3' }));
    expect(anchor).toEqual({ track_id: 'track-3' });
    expect(anchor).not.toHaveProperty('shape_id');
  });

  it('anchors to nothing when the shape has not been saved yet', () => {
    // A pending shape's id is client-side only; the server has never seen it.
    expect(anchorFor(annotation({ id: 'tmp-1', pending: true }))).toEqual({});
  });

  it('anchors to nothing when nothing is selected', () => {
    expect(anchorFor(null)).toEqual({});
    expect(anchorFor(undefined)).toEqual({});
  });
});

describe('describeAnchor', () => {
  it('says which frame', () => {
    expect(describeAnchor(issue({ frame: 12 }))).toBe('frame 12');
  });

  it('says when it points at an object, for either kind of anchor', () => {
    expect(describeAnchor(issue({ frame: 3, shape_id: 's' }))).toBe('frame 3 · on an object');
    expect(describeAnchor(issue({ frame: 3, track_id: 't' }))).toBe('frame 3 · on an object');
  });

  it('prefers "pinned", which is the more specific thing to say', () => {
    expect(describeAnchor(issue({ frame: 3, shape_id: 's', position: [10, 20] })))
      .toBe('frame 3 · pinned');
  });
});

describe('hasPin', () => {
  it('accepts a real pair of coordinates, including the origin', () => {
    expect(hasPin(issue({ position: [10, 20] }))).toBe(true);
    expect(hasPin(issue({ position: [0, 0] }))).toBe(true);
  });

  it('rejects anything that would place a marker at NaN', () => {
    // `position` is free-form JSON server-side and defaults to `[]`. A marker drawn at NaN
    // paints nothing at all, so the annotator hunts for a pin that was never there.
    expect(hasPin(issue({ position: [] }))).toBe(false);
    expect(hasPin(issue({ position: [10] }))).toBe(false);
    expect(hasPin(issue({ position: [10, Number.NaN] }))).toBe(false);
    expect(hasPin({ ...issue(), position: [10, null] } as unknown as Issue)).toBe(false);
    expect(hasPin({ ...issue(), position: undefined } as unknown as Issue)).toBe(false);
  });
});

describe('issuePins', () => {
  it('draws only the pins belonging to the frame on screen', () => {
    // The same coordinates mean a different place on a different picture.
    const pins = issuePins(
      [
        issue({ id: 'here', frame: 4, position: [10, 20] }),
        issue({ id: 'elsewhere', frame: 9, position: [10, 20] }),
      ],
      4,
    );
    expect(pins.map((pin) => pin.id)).toEqual(['here']);
    expect(pins[0]).toMatchObject({ x: 10, y: 20, resolved: false, active: false });
  });

  it('skips an issue with no usable point rather than drawing at NaN', () => {
    expect(issuePins([issue({ frame: 0, position: [] })], 0)).toEqual([]);
  });

  it('still draws a resolved pin, marked, because "where was it" outlives the fix', () => {
    const pins = issuePins([issue({ frame: 0, position: [1, 2], state: 'resolved' })], 0);
    expect(pins[0]?.resolved).toBe(true);
  });

  it('marks the open thread so the row and the pin agree', () => {
    const pins = issuePins(
      [issue({ id: 'a', frame: 0, position: [1, 2] }), issue({ id: 'b', frame: 0, position: [3, 4] })],
      0,
      'b',
    );
    expect(pins.find((pin) => pin.id === 'a')?.active).toBe(false);
    expect(pins.find((pin) => pin.id === 'b')?.active).toBe(true);
  });

  it('survives no issues at all', () => {
    expect(issuePins(undefined, 0)).toEqual([]);
  });
});
