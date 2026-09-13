/**
 * A guard on `vite.config.ts` that the type checker cannot provide for itself.
 *
 * The config carries a `test` block, which belongs to vitest rather than vite. Vite's own
 * `defineConfig` does not declare that key — it only *appears* to accept it when some other
 * file in the project has imported vitest and pulled in its type augmentation. Test files do
 * that, so on a developer's machine everything typechecks.
 *
 * `.dockerignore` excludes every `__tests__` directory, so the container build has no such
 * file at all. The
 * augmentation never loads, `test` becomes an unknown property, and `npm run build` fails —
 * in CI only, on a config that is green locally. That is exactly the shape of bug that sits
 * red for a while because nobody can reproduce it.
 *
 * Importing `defineConfig` from `vitest/config` makes the type correct on its own terms.
 * This test exists because the import looks redundant and is the kind of thing a tidy-up
 * would "fix" straight back into a broken container build.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

const config = readFileSync(path.resolve(__dirname, '../../vite.config.ts'), 'utf8');

describe('vite.config.ts', () => {
  it('imports defineConfig from vitest, not vite', () => {
    expect(config).toMatch(/import\s*\{\s*defineConfig\s*\}\s*from\s*'vitest\/config'/);
    expect(config).not.toMatch(/import\s*\{\s*defineConfig\s*\}\s*from\s*'vite'/);
  });

  it('still has the test block that makes the import necessary', () => {
    // If the `test` block ever moves to its own vitest.config.ts, this guard is obsolete
    // and should be deleted rather than left asserting something nobody depends on.
    expect(config).toMatch(/^\s*test:\s*\{/m);
  });
});
