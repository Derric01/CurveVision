/**
 * Reading the connection the desktop shell injects.
 *
 * This is the whole integration surface between the shell and the application, and it is
 * the one place where a mistake means either a dead-end sign-in screen inside the desktop
 * app, or a browser tricked into skipping authentication. Both directions are tested.
 */

import { describe, expect, it } from 'vitest';
import { parseConnection } from '@/desktop';

const valid = {
  url: 'http://127.0.0.1:49312',
  token: 'cv_abc123_secret',
  data_dir: '/home/someone/.local/share/CurveVision',
  version: '0.1.0',
  desktop: true,
};

describe('parseConnection', () => {
  it('reads what the shell actually injects', () => {
    expect(parseConnection(valid)).toEqual({
      url: 'http://127.0.0.1:49312',
      token: 'cv_abc123_secret',
      dataDir: '/home/someone/.local/share/CurveVision',
      version: '0.1.0',
    });
  });

  it('returns null in an ordinary browser, where the global is absent', () => {
    expect(parseConnection(undefined)).toBeNull();
    expect(parseConnection(null)).toBeNull();
  });

  it('ignores values that are not objects', () => {
    for (const junk of ['string', 42, true, Symbol('x'), () => {}]) {
      expect(parseConnection(junk)).toBeNull();
    }
  });

  it('requires the desktop flag to be exactly true, not merely truthy', () => {
    // A stray global must not be able to put the app into a mode where it stops asking
    // who you are.
    for (const flag of ['true', 1, {}, [], 'yes']) {
      expect(parseConnection({ ...valid, desktop: flag })).toBeNull();
    }
    expect(parseConnection({ ...valid, desktop: false })).toBeNull();
    const { desktop: _omitted, ...withoutFlag } = valid;
    expect(parseConnection(withoutFlag)).toBeNull();
  });

  it('requires a usable token', () => {
    expect(parseConnection({ ...valid, token: '' })).toBeNull();
    expect(parseConnection({ ...valid, token: '   ' })).toBeNull();
    expect(parseConnection({ ...valid, token: 42 })).toBeNull();
    const { token: _omitted, ...withoutToken } = valid;
    expect(parseConnection(withoutToken)).toBeNull();
  });

  it('trims a token that arrived with whitespace', () => {
    expect(parseConnection({ ...valid, token: '  cv_x  ' })?.token).toBe('cv_x');
  });

  it('tolerates missing cosmetic fields rather than failing the whole connection', () => {
    // The shell gaining or dropping an informational field must not stop the app working.
    const connection = parseConnection({ token: 'cv_x', desktop: true });
    expect(connection).toEqual({ url: '', token: 'cv_x', dataDir: '', version: '' });
  });

  it('ignores cosmetic fields of the wrong type instead of trusting them', () => {
    const connection = parseConnection({ ...valid, url: 99, version: null, data_dir: [] });
    expect(connection).toMatchObject({ url: '', version: '', dataDir: '' });
    expect(connection?.token).toBe('cv_abc123_secret');
  });
});
