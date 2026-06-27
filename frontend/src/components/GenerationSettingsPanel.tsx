import { useEffect, useState } from "react";

import {
  AdminAuthError,
  errorMessage,
  getComposeSettings,
  updateComposeSettings,
  type ComposeSettings,
} from "../api/admin";

interface GenerationSettingsPanelProps {
  onExpired: () => void;
}

// The operator-set composer provider and model. Lists the providers the server can build
// (those with a key in its env, plus Ollama off public deployments), saves the choice,
// and flags a saved provider that is no longer available so it does not silently fail.
export function GenerationSettingsPanel({ onExpired }: GenerationSettingsPanelProps) {
  const [settings, setSettings] = useState<ComposeSettings | null>(null);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let active = true;
    getComposeSettings()
      .then((current) => {
        if (!active) return;
        setSettings(current);
        setProvider(current.provider ?? current.available_providers[0] ?? "");
        setModel(current.model ?? "");
      })
      .catch((err) => {
        if (!active) return;
        if (err instanceof AdminAuthError) onExpired();
        else setError(errorMessage(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [onExpired]);

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await updateComposeSettings(provider, model.trim() || null);
      setSettings(updated);
      setProvider(updated.provider ?? "");
      setModel(updated.model ?? "");
      setSaved(true);
    } catch (err) {
      if (err instanceof AdminAuthError) onExpired();
      else setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <p className="text-stone-600">Loading settings…</p>;
  if (!settings) {
    return (
      <p role="alert" className="text-sm text-red-700">
        {error ?? "Couldn't load the composer settings."}
      </p>
    );
  }

  const available = settings.available_providers;
  const savedUnavailable = settings.provider !== null && !available.includes(settings.provider);
  // Keep the saved-but-unavailable provider selectable, so the operator sees what is set.
  const options = savedUnavailable && settings.provider ? [settings.provider, ...available] : available;

  return (
    <form
      onSubmit={(e) => void save(e)}
      className="rounded border border-stone-200 bg-white p-5 space-y-3"
    >
      <p className="text-sm text-stone-600">
        The provider and model the composer uses, for both the live triggers and the
        nightly cron. API keys stay in the server environment and never travel here.
      </p>
      {savedUnavailable && (
        <p role="alert" className="text-sm text-amber-700">
          The saved provider <span className="font-medium">{settings.provider}</span> has no key
          configured on the server. Pick an available one, or add its key and reload.
        </p>
      )}
      <label className="block">
        <span className="text-xs uppercase tracking-wide text-stone-500">Provider</span>
        <select
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          className="mt-1 w-full rounded border border-stone-300 px-2.5 py-1.5 text-sm focus:outline-none focus:border-emerald-700"
        >
          {options.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        <span className="text-xs uppercase tracking-wide text-stone-500">Model</span>
        <input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="provider default"
          className="mt-1 w-full rounded border border-stone-300 px-2.5 py-1.5 text-sm focus:outline-none focus:border-emerald-700"
        />
      </label>
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      {saved && <p className="text-sm text-emerald-700">Saved.</p>}
      <button
        type="submit"
        disabled={saving || !provider}
        className="rounded bg-emerald-800 text-white px-4 py-2 text-sm disabled:opacity-50 enabled:cursor-pointer"
      >
        {saving ? "Saving…" : "Save"}
      </button>
    </form>
  );
}
