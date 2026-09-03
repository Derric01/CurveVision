/** Authentication state. Small enough not to need a reducer. */

import { create } from 'zustand';
import { api, tokenStore } from '@/api/client';
import type { User } from '@/api/types';

interface SessionState {
  user: User | null;
  status: 'unknown' | 'authenticated' | 'anonymous';
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

  /** Resolve the stored token into a user, or fall back to anonymous. */
  async restore() {
    if (!tokenStore.access) {
      set({ status: 'anonymous', user: null });
      return;
    }
    try {
      set({ user: await api.me(), status: 'authenticated', error: null });
    } catch {
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
