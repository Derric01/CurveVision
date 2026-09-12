/**
 * Running inside the desktop application.
 *
 * The desktop shell spawns the local server, then opens this page at that server's address
 * with an initialization script that runs before any of our own code:
 *
 *     window.__CURVEVISION__ = { url, token, data_dir, version, desktop: true }
 *
 * That is the entire integration surface. The same bundle is served to browsers against a
 * shared server, where the global is simply absent and everything behaves as it always has
 * — a real sign-in screen, because there really are other people.
 *
 * The value is read **once, at module load**, deliberately: it is injected before the page
 * scripts run, so it is available before React mounts. That is what lets the application
 * skip rendering a signed-out state it would immediately have to replace.
 */

export interface DesktopConnection {
  /** Where the local server is listening. Informational: the page is same-origin with it. */
  readonly url: string;
  /** An API token minted for this launch. Never persisted — see `parseConnection`. */
  readonly token: string;
  /** Where this installation keeps its database and media. Shown, never used as a path. */
  readonly dataDir: string;
  readonly version: string;
}

declare global {
  interface Window {
    __CURVEVISION__?: unknown;
  }
}

/**
 * Validate whatever the shell injected.
 *
 * Only two fields are load-bearing: the `desktop` flag, and a token. The rest is
 * informational, so a shell that grows a field or drops a cosmetic one does not break the
 * application. Anything that fails this returns `null` and the app falls back to the
 * ordinary browser path, which is the safe direction to fail in.
 */
export function parseConnection(source: unknown): DesktopConnection | null {
  if (typeof source !== 'object' || source === null) return null;
  const value = source as Record<string, unknown>;

  // Not a truthiness check: a stray global must not be able to put the app into a mode
  // where it stops asking who you are.
  if (value.desktop !== true) return null;

  const token = typeof value.token === 'string' ? value.token.trim() : '';
  if (!token) return null;

  return {
    url: typeof value.url === 'string' ? value.url : '',
    token,
    dataDir: typeof value.data_dir === 'string' ? value.data_dir : '',
    version: typeof value.version === 'string' ? value.version : '',
  };
}

function read(): DesktopConnection | null {
  // `typeof window` guards the test environment, which runs in Node with no DOM.
  if (typeof window === 'undefined') return null;
  return parseConnection(window.__CURVEVISION__);
}

/** The desktop connection, or `null` when running in an ordinary browser. */
export const desktop: DesktopConnection | null = read();

export function isDesktop(): boolean {
  return desktop !== null;
}

/**
 * The credential to authenticate with, when there is one.
 *
 * Never written to `localStorage`. The shell mints a fresh token on every launch and
 * revokes the previous one, so a stored copy would be a stale credential that outlives the
 * session that owned it — exactly the failure the per-launch rotation exists to prevent.
 */
export function desktopToken(): string | null {
  return desktop?.token ?? null;
}
