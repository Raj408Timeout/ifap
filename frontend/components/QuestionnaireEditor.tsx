"use client";

import type { AgentTrace, Question, Questionnaire, ValidationSummary } from "@/lib/types";
import { QuestionCard } from "./QuestionCard";

interface Props {
  questionnaire: Questionnaire;
  validation: ValidationSummary;
  trace: AgentTrace[];
  dirty: boolean;
  busy: boolean;
  onChange: (questionnaire: Questionnaire) => void;
  onSave: () => void;
  onPublish: () => void;
}

function move<T>(items: T[], from: number, delta: number): T[] {
  const to = from + delta;
  if (to < 0 || to >= items.length) return items;
  const copy = [...items];
  const [item] = copy.splice(from, 1);
  if (item !== undefined) copy.splice(to, 0, item);
  return copy;
}

export function QuestionnaireEditor(props: Props) {
  const { questionnaire, validation, trace, dirty, busy, onChange, onSave, onPublish } = props;
  const editable = questionnaire.status === "draft";
  const setQuestions = (questions: Question[]) => onChange({ ...questionnaire, questions });

  return (
    <section className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex-1">
            <input
              value={questionnaire.title}
              disabled={!editable}
              onChange={(event) => onChange({ ...questionnaire, title: event.target.value })}
              aria-label="Questionnaire title"
              className="w-full rounded border border-transparent px-2 py-1 text-xl font-semibold hover:border-slate-200 focus:border-indigo-500 focus:outline-none disabled:bg-transparent"
            />
            <p className="px-2 text-sm text-slate-500">
              {questionnaire.survey_type} · v{questionnaire.version} · {questionnaire.status} ·{" "}
              {questionnaire.questions.length} questions
            </p>
          </div>
          {editable && (
            <div className="flex gap-2">
              <button type="button" onClick={onSave} disabled={!dirty || busy} className="rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium disabled:opacity-50">
                Save changes
              </button>
              <button type="button" onClick={onPublish} disabled={dirty || busy || !validation.is_valid} className="rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50">
                Publish
              </button>
            </div>
          )}
        </div>
        <ValidationBanner validation={validation} />
      </div>

      <ol className="space-y-2">
        {questionnaire.questions.map((question, index) => (
          <QuestionCard
            key={question.id}
            question={question}
            index={index}
            total={questionnaire.questions.length}
            editable={editable}
            onChange={(updated) =>
              setQuestions(questionnaire.questions.map((q, i) => (i === index ? updated : q)))
            }
            onMove={(delta) => setQuestions(move(questionnaire.questions, index, delta))}
            onRemove={() => setQuestions(questionnaire.questions.filter((_, i) => i !== index))}
          />
        ))}
      </ol>

      <TracePanel trace={trace} />
    </section>
  );
}

function ValidationBanner({ validation }: { validation: ValidationSummary }) {
  if (validation.issues.length === 0) {
    return <p className="mt-3 px-2 text-sm text-emerald-700">✓ Validation Agent: no issues found</p>;
  }
  return (
    <ul className="mt-3 space-y-1 px-2 text-sm">
      {validation.issues.map((issue, index) => (
        <li key={index} className={issue.severity === "error" ? "text-red-700" : "text-amber-700"}>
          {issue.severity === "error" ? "✕" : "!"} {issue.message}
        </li>
      ))}
    </ul>
  );
}

function TracePanel({ trace }: { trace: AgentTrace[] }) {
  if (trace.length === 0) return null;
  return (
    <details className="rounded-xl border border-slate-200 bg-white p-4 text-sm">
      <summary className="cursor-pointer font-medium">Agent execution trace</summary>
      <table className="mt-3 w-full text-left">
        <thead className="text-xs text-slate-500">
          <tr>
            <th className="py-1">Agent</th>
            <th>Strategy</th>
            <th>Model</th>
            <th>Attempts</th>
            <th>Duration</th>
            <th>Note</th>
          </tr>
        </thead>
        <tbody>
          {trace.map((step) => (
            <tr key={step.agent} className="border-t border-slate-100">
              <td className="py-1 font-mono">
                {step.status === "succeeded" ? "✓" : "✕"} {step.agent}@{step.version}
              </td>
              <td>{step.strategy}</td>
              <td className="font-mono text-xs">
                {step.models.length > 0 ? step.models.join(", ") : <span className="text-slate-400">no LLM</span>}
              </td>
              <td>{step.attempts}</td>
              <td>{step.duration_ms.toFixed(1)} ms</td>
              <td className="text-slate-500">{step.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
