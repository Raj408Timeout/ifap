"use client";

import type { AnswerType, Question } from "@/lib/types";

const TYPE_LABELS: Record<AnswerType, string> = {
  boolean: "Yes / No",
  multiple_choice: "Multiple choice",
  single_choice: "Single choice",
  rich_text: "Rich text",
  numeric: "Numeric",
  date: "Date",
  file_upload: "File upload",
};

interface Props {
  question: Question;
  index: number;
  total: number;
  editable: boolean;
  onChange: (question: Question) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
}

export function QuestionCard({ question, index, total, editable, onChange, onMove, onRemove }: Props) {
  const rule = question.dependency_rules[0];

  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-start gap-3">
        <span className="mt-2 w-6 shrink-0 text-sm font-semibold text-slate-400">{index + 1}</span>
        <div className="flex-1 space-y-2">
          <input
            value={question.label}
            disabled={!editable}
            onChange={(event) => onChange({ ...question, label: event.target.value })}
            aria-label={`Question ${index + 1} text`}
            className="w-full rounded border border-transparent px-2 py-1 font-medium hover:border-slate-200 focus:border-indigo-500 focus:outline-none disabled:bg-transparent"
          />
          <div className="flex flex-wrap gap-2 px-2 text-xs">
            <span className="rounded bg-indigo-50 px-2 py-0.5 text-indigo-700">
              {TYPE_LABELS[question.answer_type]}
            </span>
            <span className="rounded bg-slate-100 px-2 py-0.5 text-slate-600">{question.category}</span>
            {rule && (
              <span className="rounded bg-amber-50 px-2 py-0.5 text-amber-700">
                Shown if {rule.depends_on} = {String(rule.value)}
              </span>
            )}
            <span className="text-slate-400">{question.id}</span>
          </div>
          {question.choices.length > 0 && (
            <ul className="flex flex-wrap gap-1 px-2">
              {question.choices.map((choice) => (
                <li key={choice.value} className="rounded-full border border-slate-200 px-2 py-0.5 text-xs">
                  {choice.label}
                </li>
              ))}
            </ul>
          )}
        </div>
        {editable && (
          <div className="flex shrink-0 flex-col gap-1 text-xs">
            <button type="button" onClick={() => onMove(-1)} disabled={index === 0} className="rounded px-2 py-1 hover:bg-slate-100 disabled:opacity-30" aria-label="Move up">
              ↑
            </button>
            <button type="button" onClick={() => onMove(1)} disabled={index === total - 1} className="rounded px-2 py-1 hover:bg-slate-100 disabled:opacity-30" aria-label="Move down">
              ↓
            </button>
            <button type="button" onClick={onRemove} className="rounded px-2 py-1 text-red-600 hover:bg-red-50" aria-label="Remove question">
              ✕
            </button>
          </div>
        )}
      </div>
    </li>
  );
}
