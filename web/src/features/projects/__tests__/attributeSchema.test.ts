/**
 * Editing a label's attributes: what the form sends, and what it will not let through.
 *
 * The server refuses an edit that would strand values already recorded (see
 * `TestEditingAttributes` in `server/tests/api/test_label_schema.py`). What is pinned here
 * is the browser's half: an existing attribute travels with its id, its saved options are
 * never dropped, and the three things that would only come back as an unreadable 422 — a
 * blank or repeated name, a select with no options, an unusable default — are caught
 * before Save.
 */

import { describe, expect, it } from 'vitest';
import type { AttributeDefinition } from '@/api/types';
import {
  attributeProblem,
  blankAttribute,
  defaultChoices,
  draftFromAttribute,
  draftToPayload,
  isSaved,
  optionsOf,
  parseOptions,
} from '../attributeSchema';

function attribute(overrides: Partial<AttributeDefinition> = {}): AttributeDefinition {
  return {
    id: 'attribute-1',
    name: 'colour',
    attribute_type: 'select',
    values: ['red', 'blue'],
    default_value: 'red',
    mutable: false,
    required: false,
    position: 0,
    ...overrides,
  };
}

describe('draftFromAttribute and draftToPayload', () => {
  it('round-trips an existing attribute unchanged, id and all', () => {
    // The id is what makes the server keep the definition — and the values recorded under
    // it — rather than delete it and create a new one of the same name.
    const saved = attribute();
    expect(draftToPayload(draftFromAttribute(saved), 0)).toEqual({
      id: 'attribute-1',
      name: 'colour',
      attribute_type: 'select',
      values: ['red', 'blue'],
      default_value: 'red',
      mutable: false,
      required: false,
      position: 0,
    });
  });

  it('sends no id for an attribute the form added', () => {
    const payload = draftToPayload({ ...blankAttribute(), name: 'occluded' }, 2);
    expect('id' in payload).toBe(false);
    expect(payload).toMatchObject({ name: 'occluded', attribute_type: 'checkbox', position: 2 });
  });

  it('carries `required` through untouched, since the form does not offer it', () => {
    const payload = draftToPayload(draftFromAttribute(attribute({ required: true })), 0);
    expect(payload.required).toBe(true);
  });

  it('normalises the name the way a label name is normalised', () => {
    const draft = { ...draftFromAttribute(attribute()), name: '  paint   colour ' };
    expect(draftToPayload(draft, 0).name).toBe('paint colour');
  });

  it('reads a checkbox default in lowercase, so "False" is not dropped on save', () => {
    const draft = draftFromAttribute(
      attribute({ attribute_type: 'checkbox', values: [], default_value: 'False' }),
    );
    expect(draftToPayload(draft, 0).default_value).toBe('false');
  });

  it('sends a blank default as no default', () => {
    const draft = { ...blankAttribute(), name: 'notes', attribute_type: 'text' as const };
    expect(draftToPayload({ ...draft, default_value: '   ' }, 0).default_value).toBeNull();
  });

  it('drops a default that is no longer one of the choices', () => {
    // A new attribute switched from checkbox to select would otherwise send "true" as the
    // default of a select whose options are "a" and "b" — which the server refuses, and
    // takes the whole label down with it.
    const draft = {
      ...blankAttribute(),
      name: 'size',
      attribute_type: 'select' as const,
      addedOptions: 'a, b',
      default_value: 'true',
    };
    expect(draftToPayload(draft, 0).default_value).toBeNull();
  });

  it('drops an invalid default an older version of the server accepted', () => {
    // Before defaults were validated one could be stored outside the options. Sending it
    // back verbatim is now a 422, so a label carrying one could not even be renamed.
    const draft = draftFromAttribute(attribute({ default_value: 'green' }));
    expect(draftToPayload(draft, 0).default_value).toBeNull();
  });
});

describe('options', () => {
  it('parses a comma-separated list, trimmed, without blanks or repeats', () => {
    expect(parseOptions(' red, blue,, red ,black, ')).toEqual(['red', 'blue', 'black']);
    expect(parseOptions('')).toEqual([]);
  });

  it('appends new options after the saved ones and never drops a saved one', () => {
    const draft = { ...draftFromAttribute(attribute()), addedOptions: 'silver, red' };
    // "red" is already saved: it is not repeated, and "blue" is still there.
    expect(optionsOf(draft)).toEqual(['red', 'blue', 'silver']);
  });

  it('leaves the values of a non-choice attribute exactly as the server had them', () => {
    const draft = draftFromAttribute(
      attribute({ attribute_type: 'number', values: ['0', '10'], default_value: null }),
    );
    expect(optionsOf({ ...draft, addedOptions: 'ignored' })).toEqual(['0', '10']);
  });

  it('offers a checkbox true and false, a select its options, and text nothing', () => {
    expect(defaultChoices({ ...blankAttribute(), attribute_type: 'checkbox' })).toEqual([
      'false',
      'true',
    ]);
    expect(defaultChoices(draftFromAttribute(attribute()))).toEqual(['red', 'blue']);
    expect(defaultChoices({ ...blankAttribute(), attribute_type: 'text' })).toBeNull();
  });
});

describe('attributeProblem', () => {
  it('accepts a schema with nothing wrong', () => {
    expect(attributeProblem([draftFromAttribute(attribute())])).toBeNull();
    expect(attributeProblem([])).toBeNull();
  });

  it('refuses a blank name', () => {
    expect(attributeProblem([{ ...blankAttribute(), name: '   ' }])).toMatch(/needs a name/);
  });

  it('refuses two attributes of one name, after normalising both', () => {
    // Values are stored by name, so two `colour`s would share one value.
    const drafts = [
      draftFromAttribute(attribute()),
      { ...blankAttribute(), name: ' colour ' },
    ];
    expect(attributeProblem(drafts)).toBe('Two attributes are named "colour".');
  });

  it('refuses a select or radio with no options', () => {
    const draft = { ...blankAttribute(), name: 'size', attribute_type: 'radio' as const };
    expect(attributeProblem([draft])).toBe('"size" needs at least one option.');
    expect(attributeProblem([{ ...draft, addedOptions: 's, m, l' }])).toBeNull();
  });

  it('refuses a number default that is not a number', () => {
    const draft = { ...blankAttribute(), name: 'count', attribute_type: 'number' as const };
    expect(attributeProblem([{ ...draft, default_value: 'many' }])).toMatch(/must be a number/);
    expect(attributeProblem([{ ...draft, default_value: '2.5' }])).toBeNull();
  });
});

describe('isSaved', () => {
  it('tells an attribute the server has from one the form is adding', () => {
    expect(isSaved(draftFromAttribute(attribute()))).toBe(true);
    expect(isSaved(blankAttribute())).toBe(false);
  });
});
