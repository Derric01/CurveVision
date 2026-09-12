/** Authentication state. Small enough not to need a reducer. */

import { create } from 'zustand';
import { api, tokenStore } from '@/api/client';
import { isDesktop } from '@/desktop';
import type { User } from '@/api/types';

interface SessionState {
  user: User | null;
  /**
   * `unavailable` exists for the desktop application only. There, a failed restore is not
   * a signed-out user — it is a local server that did not answer, and offering a sign-in
   * form would be a dead end because the person has no password to type.
   */
  status: 'unknown' | 'authenticated' | 'anonymous' | 'unavailable';
  error: string | null;
  restore: () => Promise<void>;
  login: (identifier: string, password: string) => Promise<void>;
  register: (input: {
    email: string;
    username: string;
    password: string;
    full_name?: string;
  }) => Promise<void>;
  logout: () => Promise<void>;
}

export const useSession = create<SessionState>((set) => ({
  user: null,
  status: 'unknown',
  error: null,

  /** Resolve the available credential into a user.
   *
   * In the desktop application the credential was injected by the shell before this
   * script ran, so there is nothing stored to look for and nothing to clear on failure.
   */
  async restore() {
    if (!isDesktop() && !tokenStore.access) {
      set({ status: 'anonymous', user: null });
      return;
    }
    try {
      set({ user: await api.me(), status: 'authenticated', error: null });
    } catch (error) {
      if (isDesktop()) {
        set({
          status: 'unavailable',
          user: null,
          error: error instanceof Error ? error.message : 'The local server did not respond',
        });
        return;
      }
      tokenStore.clear();
      set({ status: 'anonymous', user: null });
    }
  },

  async login(identifier, password) {
    set({ error: null });
    try {
      const user = await api.login(identifier, password);
      set({ user, status: 'authenticated' });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : 'Sign-in failed' });
      throw error;
    }
  },

  async register(input) {
    set({ error: null });
    try {
      const user = await api.register(input);
      set({ user, status: 'authenticated' });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : 'Registration failed' });
      throw error;
    }
  },

  async logout() {
    await api.logout();
    set({ user: null, status: 'anonymous' });
  },
}));
