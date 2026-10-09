"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { LLMStatus, ModelState } from "@/lib/types";

function waitText(model: ModelState): string {
  const seconds = model.available_in_seconds;
  if (seconds === null) return model.reason ?? "unavailable";
  if (seconds >= 3600) return `${model.reason ?? "unavailable"} · back in ${Math.round(seconds / 3600)}h`;
  if (seconds >= 60) return `${model.reason ?? "unavailable"} · back in ${Math.round(seconds / 60)}m`;
  return `${model.reason ?? "unavailable"} · back in ${seconds}s`;
}

interface Props {
  /** Changing this number re-reads the status (e.g. after every generation). */
  refresh: number;
}

/**
 * Header switch for LLM-backed agent strategies, plus the model chain: the model the next
 * call will use, and each fallback's state (quota exhausted, cooling down, ...).
 */
export function LlmToggle({ refresh }: Props) {
  const [status, setStatus] = useState<LLMStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    api.llmStatus().then(
      (next) => {
        setStatus(next);
        setError(null);
      },
      () => setError("API unreachable"),
    );
  }, [refresh]);

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
      <span className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-500" title="Set IFAP_LLM__PROVIDER (ollama or gemini) and restart the API">
        AI model: not configured · heuristics only
      </span>
    );
  }

  const allDown = status.models.length > 0 && status.models.every((m) => !m.available);

  return (
    <div className="relative flex items-center gap-1 text-xs">
      <button
        type="button"
        role="switch"
        aria-checked={status.enabled}
        onClick={toggle}
        className="flex items-center gap-2 rounded-l-lg border border-slate-200 bg-white px-3 py-1.5"
      >
        <span className={`relative h-4 w-7 rounded-full transition ${status.enabled ? "bg-indigo-600" : "bg-slate-300"}`}>
          <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-white transition ${status.enabled ? "left-3.5" : "left-0.5"}`} />
        </span>
        <span>
          AI model <span className="font-mono">{status.model}</span> ({status.provider}):{" "}
          <strong className={allDown ? "text-amber-700" : ""}>
            {!status.enabled ? "off - heuristics" : allDown ? "all models unavailable - heuristics" : "on"}
          </strong>
        </span>
      </button>
      {status.models.length > 1 && (
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          className="rounded-r-lg border border-l-0 border-slate-200 bg-white px-2 py-1.5"
          title="Show the model fallback chain"
        >
          {status.models.filter((m) => m.available).length}/{status.models.length} ▾
        </button>
      )}
      {open && (
        <ol className="absolute right-0 top-full z-10 mt-1 w-80 space-y-1 rounded-lg border border-slate-200 bg-white p-3 shadow">
          <li className="pb-1 text-slate-500">Fallback chain (tried in this order)</li>
          {status.models.map((model, index) => (
            <li key={model.name} className="flex items-start gap-2">
              <span className={model.available ? "text-emerald-600" : "text-amber-600"}>{model.available ? "●" : "○"}</span>
              <span>
                <span className="font-mono">
                  {index + 1}. {model.name}
                </span>
                {!model.available && <span className="block text-slate-500">{waitText(model)}</span>}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
