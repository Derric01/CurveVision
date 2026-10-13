/**
 * The client's own behaviour, against a `fetch` that records what it was asked.
 * `live.test.ts` runs the same client against a real server.
 */

import { describe, expect, it } from 'vitest';
import { createClient, CurveVisionError } from '../src/index';

function recording(status: number, body: unknown, contentType = 'application/json') {
  const seen: Request[] = [];
  const fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init);
    seen.push(request);
    return new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': contentType },
    });
  };
  return { seen, fetch: fetch as typeof globalThis.fetch };
}

describe('createClient', () => {
  it('sends the token as a bearer header, to the path the schema names', async () => {
    const { seen, fetch } = recording(200, { id: 'p1' });
    const cv = createClient({ baseUrl: 'https://cv.example.com/', token: 'cv_abc', fetch });

    const project = await cv.call(
      cv.api.GET('/api/v1/projects/{project_id}', {
        params: { path: { project_id: 'p1' } },
      }),
    );

    expect(project).toEqual({ id: 'p1' });
    expect(seen[0]?.url).toBe('https://cv.example.com/api/v1/projects/p1');
    expect(seen[0]?.headers.get('authorization')).toBe('Bearer cv_abc');
  });

  it('asks a token function on every request', async () => {
    const { seen, fetch } = recording(200, []);
    let n = 0;
    const cv = createClient({ baseUrl: 'https://cv', token: () => `t${(n += 1)}`, fetch });

    await cv.call(cv.api.GET('/api/v1/organizations'));
    await cv.call(cv.api.GET('/api/v1/organizations'));

    expect(seen.map((r) => r.headers.get('authorization'))).toEqual(['Bearer t1', 'Bearer t2']);
  });

  it("throws the server's problem document as a CurveVisionError", async () => {
    const problem = {
      type: 'https://curvevision.dev/errors/not_found',
      title: 'Not found',
      status: 404,
      detail: 'Project not found',
    };
    const { fetch } = recording(404, problem, 'application/problem+json');
    const cv = createClient({ baseUrl: 'https://cv', fetch });

    const failed = cv.call(
      cv.api.GET('/api/v1/projects/{project_id}', { params: { path: { project_id: 'x' } } }),
    );

    await expect(failed).rejects.toBeInstanceOf(CurveVisionError);
    await expect(failed).rejects.toMatchObject({ status: 404, message: 'Project not found' });
  });
});
