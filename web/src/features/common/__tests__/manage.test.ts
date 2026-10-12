import { describe, expect, it } from 'vitest';
import { canDelete, canRename, deleteConfirmed, deleteConsequence, renamed } from '../manage';

describe('who may rename and delete', () => {
  it('mirrors the policy engine', () => {
    expect(canRename('maintainer')).toBe(true);
    expect(canRename('reviewer')).toBe(false);
    expect(canDelete('task', 'maintainer')).toBe(true);
    expect(canDelete('project', 'maintainer')).toBe(false);
    expect(canDelete('project', 'admin')).toBe(true);
  });

  it('leaves an unreported role to the server', () => {
    expect(canRename(undefined)).toBe(true);
    expect(canDelete('project', undefined)).toBe(true);
  });
});

describe('renaming', () => {
  it('saves a tidied new name, and nothing for no change', () => {
    expect(renamed('Batch 1', '  Batch   2 ', 300)).toBe('Batch 2');
    expect(renamed('Batch 1', 'Batch 1 ', 300)).toBeNull();
    expect(renamed('Batch 1', '   ', 300)).toBeNull();
    expect(renamed('a', 'x'.repeat(301), 300)).toBeNull();
  });
});

describe('deleting', () => {
  it('needs the name typed exactly', () => {
    expect(deleteConfirmed('Street scenes', 'Street scenes')).toBe(true);
    expect(deleteConfirmed('Street scenes', ' Street scenes ')).toBe(true);
    expect(deleteConfirmed('Street scenes', 'street scenes')).toBe(false);
    expect(deleteConfirmed('Street scenes', '')).toBe(false);
  });

  it('says what goes with it', () => {
    expect(deleteConsequence('project', { tasks: 3 })).toMatch(
      /deletes its 3 tasks, their jobs, annotations, reviews and dataset versions.*cannot be undone/,
    );
    expect(deleteConsequence('task', { frames: 1 })).toMatch(/its 1 frame, jobs/);
    expect(deleteConsequence('task', { frames: 1 })).toMatch(/unless another task uses them/);
  });
});
