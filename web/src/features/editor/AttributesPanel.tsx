/**
 * The attribute values of the one selected object, editable.
 *
 * Until this existed no screen could set a value: the editor carried `attributes` through
 * every save and never let a person change one, so `occluded`, `truncated` or a car's
 * colour could be recorded only from the SDK, the CLI, an import, or by a default.
 *
 * Every change goes through `engine.setSelectionAttribute`, so it is undoable and reaches
 * autosave like a drag does — including for a tracked object, where autosave puts a
 * mutable value on the keyframe at this frame and any other on the track. Renders nothing
 * unless exactly one object is selected and its label has attributes, so an ordinary
 * rectangle-only schema pays no space for it.
 */

import { useState } from 'react';
import clsx from 'clsx';
import type { AttributeDefinition } from '@/api/types';
import { inputFromValue, isMissing, valueFromInput } from './attributeValues';

export function AttributesPanel({
  objectId,
  definitions,
  values,
  tracked,
  onChange,
}: {
  objectId: string;
  definitions: AttributeDefinition[];
  values: Record<string, unknown>;
  tracked: boolean;
  onChange: (name: string, value: unknown) => void;
}) {
  if (definitions.length === 0) return null;
  return (
    <section className="border-t border-ink-800 px-3 py-2" data-attributes-panel={objectId}>
      <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-ink-500">
        Attributes
      </h3>
      <div className="space-y-1.5">
        {definitions.map((definition) => (
          // Keyed by object too: a text box holding a half-typed number must not carry over
          // to the next object selected.
          <AttributeControl
            key={`${objectId}:${definition.id}`}
            definition={definition}
            value={values[definition.name]}
            tracked={tracked}
            onChange={(value) => onChange(definition.name, value)}
          />
        ))}
      </div>
    </section>
  );
}

function AttributeControl({
  definition,
  value,
  tracked,
  onChange,
}: {
  definition: AttributeDefinition;
  value: unknown;
  tracked: boolean;
  onChange: (value: unknown) => void;
}) {
  const shown = inputFromValue(definition, value);
  // What is typed into a number box is kept apart from what is stored, so "-" or "1." can
  // be typed on the way to a number without being refused or rewritten underneath.
  const [typed, setTyped] = useState<string | null>(null);
  // The save will be refused until this has a value, so say which control that is.
  const missing = isMissing(definition, value);
  const control = clsx(
    'h-7 w-full min-w-0 rounded border bg-ink-950 px-1.5 text-xs text-ink-100 focus:border-curve-400',
    missing ? 'border-amber-500/70' : 'border-ink-700',
  );
  const hint = tracked && definition.mutable ? 'Recorded on this frame, and held until the next keyframe changes it.' : undefined;

  const label = (
    <span className="flex items-center gap-1 text-xs text-ink-300" title={hint}>
      {definition.name}
      {definition.required && (
        <span className="text-amber-300" title={missing ? 'Required, and not set yet' : 'Required'}>
          *
        </span>
      )}
      {tracked && definition.mutable && <span className="text-[10px] text-ink-500">(this frame)</span>}
    </span>
  );
  const marks = missing ? { 'data-attribute-missing': '' } : {};

  if (definition.attribute_type === 'checkbox') {
    return (
      <label className="flex items-center gap-2" data-attribute={definition.name} {...marks}>
        <input
          type="checkbox"
          checked={shown === true}
          onChange={(event) => onChange(valueFromInput(definition, event.target.checked))}
        />
        {label}
      </label>
    );
  }

  if (definition.attribute_type === 'select' || definition.attribute_type === 'radio') {
    return (
      <label
        className="grid grid-cols-[6rem_1fr] items-center gap-2"
        data-attribute={definition.name}
        {...marks}
      >
        {label}
        <select
          className={control}
          value={definition.values.includes(String(shown)) ? String(shown) : ''}
          onChange={(event) => onChange(valueFromInput(definition, event.target.value))}
        >
          <option value="">—</option>
          {definition.values.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </label>
    );
  }

  return (
    <label
      className="grid grid-cols-[6rem_1fr] items-center gap-2"
      data-attribute={definition.name}
      {...marks}
    >
      {label}
      <input
        className={control}
        inputMode={definition.attribute_type === 'number' ? 'decimal' : undefined}
        value={typed ?? String(shown)}
        onChange={(event) => {
          setTyped(event.target.value);
          const next = valueFromInput(definition, event.target.value);
          if (next !== null) onChange(next);
        }}
        onBlur={() => setTyped(null)}
      />
    </label>
  );
}
