/**
 * Reading which entry of a save the server refused.
 *
 * The server side is pinned by `test_a_refusal_names_the_entry_it_came_from` in
 * `server/tests/api/test_workflow.py`; what autosave does with the answer is driven in a
 * browser by `scripts/verify_save_refusals.py`.
 */

import { describe, expect, it } from 'vitest';
import type { ProblemDocument } from '@/api/types';
import { refusedEntries } from '../refusals';

function problem(errors: ProblemDocument['errors']): ProblemDocument {
  return { type: 'x', title: 'Validation error', status: 422, detail: 'd', errors };
}

describe('refusedEntries', () => {
  it('reads the entry a refusal came from, with the reason as the server gave it', () => {
    const refused = problem([
      {
        location: ['body', 'created_shapes', 1],
        message: "Attribute 'plate' is required",
        type: 'validation_error',
      },
    ]);
    expect(refusedEntries(refused)).toEqual([
      { list: 'created_shapes', index: 1, reason: "Attribute 'plate' is required" },
    ]);
  });

  it('reads an entry the request itself was malformed in, naming the field', () => {
    const malformed = problem([
      {
        location: ['body', 'updated_shapes', 0, 'points'],
        message: 'polygon requires at least 3 points',
        type: 'value_error',
      },
      { location: ['body', 'updated_tracks', 2], message: 'Track x not found', type: 'not_found' },
    ]);
    expect(refusedEntries(malformed)).toEqual([
      { list: 'updated_shapes', index: 0, reason: 'points: polygon requires at least 3 points' },
      { list: 'updated_tracks', index: 2, reason: 'Track x not found' },
    ]);
  });

  it('names nothing when the whole batch was refused, or a list autosave does not send', () => {
    // Nothing to set aside, so the batch is put back whole, as it always was.
    expect(refusedEntries(problem(undefined))).toEqual([]);
    expect(
      refusedEntries(
        problem([
          { location: ['body', 'annotation_version'], message: 'bad', type: 'int_parsing' },
          { location: ['body', 'deleted_shapes', 0], message: 'bad id', type: 'uuid_parsing' },
          { location: ['body', 'created_tags', 0], message: 'bad', type: 'validation_error' },
          { location: ['query', 'created_shapes', 0], message: 'bad', type: 'x' },
        ]),
      ),
    ).toEqual([]);
  });
});
