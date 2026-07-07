import { create } from "zustand";

import {
  deleteSave,
  saveMeal,
  listSaves,
  type SaveSource,
  type SaveTarget,
} from "../api/saves";
import { useSessionStore } from "./session";

export function saveKey(source: SaveSource, sourceKey: string): string {
  return `${source}:${sourceKey}`;
}

// A lookup save keys on the client-minted per-result id, which the server
// stores verbatim as source_key — no name-derived guessing needed.
function targetKey(target: SaveTarget): string {
  return target.source === "lookup"
    ? saveKey("lookup", target.payload.lookup_id)
    : saveKey(target.source, target.sourceId);
}

// The optimistic placeholder id between the tap and the server's answer. Toggling
// while pending is ignored rather than queued; a second tap that fast is noise.
const PENDING = "pending";

interface SavedMealsState {
  status: "idle" | "loading" | "ready";
  // saveKey -> saved-meal row id, the single truth every save button reads.
  keys: Map<string, string>;
  load: () => Promise<void>;
  toggle: (target: SaveTarget) => Promise<void>;
  // Direct removal by row id, for surfaces that hold the saved row rather than
  // its source (the profile grid). Resolves false when the delete failed.
  unsave: (id: string, key: string) => Promise<boolean>;
  reset: () => void;
}

// Shared, non-persisted save state (the session-store pattern: many save buttons read it
// at once and must agree). Hydrated once per signed-in session via the subscription
// below; every toggle is optimistic with a rollback on failure.
export const useSavedMealsStore = create<SavedMealsState>()((set, get) => ({
  status: "idle",
  keys: new Map(),
  load: async () => {
    if (get().status !== "idle") return;
    if (useSessionStore.getState().status !== "authed") return;
    set({ status: "loading" });
    try {
      const items = await listSaves();
      set({
        status: "ready",
        keys: new Map(items.map((item) => [saveKey(item.source, item.source_key), item.id])),
      });
    } catch {
      // A failed hydrate (network, expired session) leaves save buttons unlit; the next
      // signed-in session retries. The 401 path already reset the session store.
      set({ status: "idle" });
    }
  },
  toggle: async (target) => {
    const key = targetKey(target);
    const { keys } = get();
    const existing = keys.get(key);
    if (existing === PENDING) return;

    if (existing !== undefined) {
      await get().unsave(existing, key);
      return;
    }

    set((state) => ({ keys: new Map(state.keys).set(key, PENDING) }));
    try {
      const created = await saveMeal(target);
      set((state) => {
        const next = new Map(state.keys);
        next.delete(key);
        // The server's key is authoritative (it echoes the stored source_key).
        next.set(saveKey(created.source, created.source_key), created.id);
        return { keys: next };
      });
    } catch {
      set((state) => {
        const next = new Map(state.keys);
        next.delete(key);
        return { keys: next };
      });
    }
  },
  unsave: async (id, key) => {
    const next = new Map(get().keys);
    next.delete(key);
    set({ keys: next });
    try {
      await deleteSave(id);
      return true;
    } catch {
      set((state) => ({ keys: new Map(state.keys).set(key, id) }));
      return false;
    }
  },
  reset: () => set({ status: "idle", keys: new Map() }),
}));

// Saves follow the session: hydrate when it authenticates, clear when it ends
// (sign-out or a dead cookie surfacing as a 401 anywhere).
useSessionStore.subscribe((session, previous) => {
  if (session.status === "authed" && previous.status !== "authed") {
    void useSavedMealsStore.getState().load();
  }
  if (session.status !== "authed" && previous.status === "authed") {
    useSavedMealsStore.getState().reset();
  }
});
