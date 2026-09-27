import { describe, expect, it } from 'vitest';
import type { AttributeDefinition } from '@/api/types';
import {
  attributesFor,
  initialAttributes,
  inputFromValue,
  isMissing,
  mutableNames,
  valueFromInput,
} from '../attributeValues';

function def(overrides: Partial<AttributeDefinition> = {}): AttributeDefinition {
  return {
    id: 'a', name: 'colour', attribute_type: 'text', values: [], default_value: null,
    mutable: false, required: false, position: 0, ...overrides,
  };
}

describe('valueFromInput', () => {
  it('stores a checkbox as a boolean, whichever spelling arrives', () => {
    const checkbox = def({ attribute_type: 'checkbox' });
    expect(valueFromInput(checkbox, true)).toBe(true);
    expect(valueFromInput(checkbox, false)).toBe(false);
    expect(valueFromInput(checkbox, 'true')).toBe(true);
  });

  it('stores a number as a number, and a number still being typed as nothing yet', () => {
    const number = def({ attribute_type: 'number' });
    expect(valueFromInput(number, '2.5')).toBe(2.5);
    expect(valueFromInput(number, '-')).toBeNull();
  });

  it('clears on a blank box rather than storing an empty string', () => {
    expect(valueFromInput(def(), '   ')).toBeUndefined();
    expect(valueFromInput(def({ attribute_type: 'number' }), '')).toBeUndefined();
    expect(valueFromInput(def({ attribute_type: 'select', values: ['a'] }), '')).toBeUndefined();
  });

  it('keeps text as typed', () => {
    expect(valueFromInput(def(), ' Ford ')).toBe(' Ford ');
  });
});

describe('inputFromValue', () => {
  it('reads a checkbox default stored as a string the same as a boolean', () => {
    const checkbox = def({ attribute_type: 'checkbox' });
    expect(inputFromValue(checkbox, 'false')).toBe(false);
    expect(inputFromValue(checkbox, 'True')).toBe(true);
    expect(inputFromValue(checkbox, true)).toBe(true);
    expect(inputFromValue(checkbox, undefined)).toBe(false);
  });

  it('shows an unset value as an empty box', () => {
    expect(inputFromValue(def(), undefined)).toBe('');
    expect(inputFromValue(def({ attribute_type: 'number' }), 3)).toBe('3');
  });
});

describe('attributesFor and mutableNames', () => {
  const labels = [
    {
      id: 'car',
      attributes: [def({ name: 'b', position: 1, mutable: true }), def({ name: 'a', position: 0 })],
      children: [{ id: 'wheel', attributes: [def({ name: 'flat' })] }],
    },
  ];

  it("lists a label's attributes in schema order, including a skeleton joint's", () => {
    expect(attributesFor(labels, 'car').map((a) => a.name)).toEqual(['a', 'b']);
    expect(attributesFor(labels, 'wheel').map((a) => a.name)).toEqual(['flat']);
    expect(attributesFor(labels, 'nothing')).toEqual([]);
  });

  it('names the mutable ones per label', () => {
    expect([...(mutableNames(labels).get('car') ?? [])]).toEqual(['b']);
  });
});

describe('initialAttributes', () => {
  it('starts from every default, typed as the server stores an explicit value', () => {
    // A default the server fills in itself is stored uncoerced — "false", the string — so
    // sending it typed is what makes a new object record the boolean.
    expect(
      initialAttributes([
        def({ name: 'parked', attribute_type: 'checkbox', default_value: 'False' }),
        def({ name: 'count', attribute_type: 'number', default_value: '2' }),
        def({ name: 'colour', attribute_type: 'select', values: ['red', 'blue'], default_value: 'blue' }),
        def({ name: 'note', default_value: 'none' }),
      ]),
    ).toEqual({ parked: false, count: 2, colour: 'blue', note: 'none' });
  });

  it('starts a required checkbox with no default unticked, which is what the box shows', () => {
    const parked = def({ name: 'parked', attribute_type: 'checkbox', required: true });
    expect(initialAttributes([parked])).toEqual({ parked: false });
    expect(initialAttributes([{ ...parked, required: false }])).toEqual({});
  });

  it('chooses nothing for a required select, text or number with no default', () => {
    // Picking the first option would record a choice nobody made.
    expect(
      initialAttributes([
        def({ name: 'colour', attribute_type: 'select', values: ['red'], required: true }),
        def({ name: 'plate', required: true }),
        def({ name: 'count', attribute_type: 'number', required: true }),
      ]),
    ).toEqual({});
  });

  it('leaves out a default the attribute would refuse, as an older server stored some', () => {
    expect(
      initialAttributes([
        def({ attribute_type: 'select', values: ['red'], default_value: 'green' }),
        def({ name: 'count', attribute_type: 'number', default_value: 'many' }),
      ]),
    ).toEqual({});
  });
});

describe('isMissing', () => {
  it('is a required attribute with no value and no default to stand in for one', () => {
    const plate = def({ name: 'plate', required: true });
    expect(isMissing(plate, undefined)).toBe(true);
    expect(isMissing(plate, 'AB12')).toBe(false);
    expect(isMissing({ ...plate, default_value: 'unknown' }, undefined)).toBe(false);
    expect(isMissing({ ...plate, required: false }, undefined)).toBe(false);
  });

  it('counts an unticked checkbox as a value', () => {
    expect(isMissing(def({ attribute_type: 'checkbox', required: true }), false)).toBe(false);
  });
});
