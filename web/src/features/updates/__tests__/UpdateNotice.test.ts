import { describe, expect, it } from 'vitest';
import { noticeFor } from '../UpdateNotice';

const base = { configured: true, current: '0.1.0', available: null, notes: null };

describe('noticeFor', () => {
  it('names the newer version and this one', () => {
    expect(noticeFor({ ...base, available: '0.2.0' })).toBe(
      'CurveVision 0.2.0 is available — this is 0.1.0.',
    );
  });

  it('says nothing when there is nothing newer, no shell, or no release key', () => {
    expect(noticeFor(base)).toBeNull();
    expect(noticeFor(null)).toBeNull();
    expect(noticeFor(undefined)).toBeNull();
    expect(noticeFor({ ...base, configured: false, available: '0.2.0' })).toBeNull();
    expect(noticeFor({ ...base, available: '0.1.0' })).toBeNull();
  });
});
