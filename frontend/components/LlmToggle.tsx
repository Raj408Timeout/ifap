"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { LLMStatus } from "@/lib/types";

/** Header switch: turns LLM-backed agent strategies on/off. Off = deterministic heuristics. */
export function LlmToggle() {
  const [status, setStatus] = useState<LLMStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.llmStatus().then(setStatus, () => setError("API unreachable"));
  }, []);

  const toggle = async () => {
    if (!status) return;
    try {
      setStatus(await api.setLlmEnabled(!status.enabled));
      setError(null);
    } catch {
      setError("Could not change the LLM switch");
    }
  };

  if (error) return <span className="text-xs text-red-600">{error}</span>;
  if (!status) return null;
  if (!status.configured) {
    return (
      <span className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-500" title="Set IFAP_LLM__PROVIDER=ollama and restart the API">
        AI model: not configured · heuristics only
      </span>
    );
  }

  return (
    <button
      type="button"
      role="switch"
      aria-checked={status.enabled}
      onClick={toggle}
      className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs"
    >
      <span className={`relative h-4 w-7 rounded-full transition ${status.enabled ? "bg-indigo-600" : "bg-slate-300"}`}>
        <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-white transition ${status.enabled ? "left-3.5" : "left-0.5"}`} />
      </span>
      <span>
        AI model <span className="font-mono">{status.model}</span> ({status.provider}):{" "}
        <strong>{status.enabled ? "on" : "off - heuristics"}</strong>
      </span>
    </button>
  );
}
