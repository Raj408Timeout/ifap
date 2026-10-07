"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface Props {
  value: string | null;
  onChange: (workflow: string) => void;
}

/** Choose which agent workflow runs: "standard" (fixed pipeline) or "autonomous" (tool loop). */
export function WorkflowPicker({ value, onChange }: Props) {
  const [workflows, setWorkflows] = useState<Record<string, string[]>>({});

  useEffect(() => {
    api.agents().then(
      (info) => {
        setWorkflows(info.workflows);
        if (value === null) onChange(info.default_workflow);
      },
      () => setWorkflows({}),
    );
  }, [value, onChange]);

  const names = Object.keys(workflows);
  if (names.length === 0 || value === null) return null;

  return (
    <label className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs">
      Workflow
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="rounded border border-slate-200 px-1 py-0.5"
        title={workflows[value]?.join(" → ")}
      >
        {names.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
    </label>
  );
}
