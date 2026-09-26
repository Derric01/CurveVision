import { describe, expect, it } from 'vitest';
import type { AttributeDefinition } from '@/api/types';
import { attributesFor, inputFromValue, mutableNames, valueFromInput } from '../attributeValues';

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
