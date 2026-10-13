/**
 * Against a real server, when `CURVEVISION_TEST_SERVER_URL` and `CURVEVISION_TEST_TOKEN`
 * name one (`scripts/with_server.py` starts one and sets both; CI does that).
 */

import { describe, expect, it } from 'vitest';
import { createClient, CurveVisionError } from '../src/index';

const url = process.env.CURVEVISION_TEST_SERVER_URL;
const token = process.env.CURVEVISION_TEST_TOKEN;

describe.skipIf(!url || !token)('against a real server', () => {
  const cv = createClient({ baseUrl: url ?? '', token });

  it('creates a project with a label and reads it back', async () => {
    const [workspace] = await cv.call(cv.api.GET('/api/v1/organizations'));
    const created = await cv.call(
      cv.api.POST('/api/v1/projects', {
        body: {
          organization_id: workspace!.id,
          slug: `ts-${Date.now()}`,
          name: 'From TypeScript',
          labels: [{ name: 'car' }],
        },
      }),
    );

    const read = await cv.call(
      cv.api.GET('/api/v1/projects/{project_id}', {
        params: { path: { project_id: created.id } },
      }),
    );

    expect(read.name).toBe('From TypeScript');
    expect((read.labels ?? []).map((label) => label.name)).toEqual(['car']);
  });

  it("refuses with the server's own reason", async () => {
    const missing = cv.call(
      cv.api.GET('/api/v1/projects/{project_id}', {
        params: { path: { project_id: '00000000-0000-0000-0000-000000000000' } },
      }),
    );
    await expect(missing).rejects.toBeInstanceOf(CurveVisionError);
    await expect(missing).rejects.toMatchObject({ status: 404 });
  });
});
