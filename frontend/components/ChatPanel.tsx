"use client";

import { useState, type FormEvent } from "react";

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
}

const EXAMPLES = [
  "A 10 question pulse survey on employee burnout and manager support",
  "Patient intake form for a cardiology clinic covering allergies and medication",
  "8 question GDPR and vendor risk compliance review",
  "Customer satisfaction survey for our online store's delivery and returns",
];

interface Props {
  messages: ChatMessage[];
  busy: boolean;
  onSend: (text: string) => void;
}

export function ChatPanel({ messages, busy, onSend }: Props) {
  const [draft, setDraft] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (text.length < 3 || busy) return;
    onSend(text);
    setDraft("");
  };

  return (
    <section className="flex h-full flex-col rounded-xl border border-slate-200 bg-white">
      <header className="border-b border-slate-200 px-4 py-3">
        <h2 className="font-semibold">Assistant</h2>
        <p className="text-xs text-slate-500">Describe the business need. The agents do the rest.</p>
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto p-4" aria-live="polite">
        {messages.length === 0 && (
          <div className="space-y-2">
            <p className="text-sm text-slate-500">Try one of these:</p>
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => onSend(example)}
                disabled={busy}
                className="block w-full rounded-lg border border-slate-200 px-3 py-2 text-left text-sm hover:border-indigo-400 hover:bg-indigo-50 disabled:opacity-50"
              >
                {example}
              </button>
            ))}
          </div>
        )}
        {messages.map((message, index) => (
          <div
            key={index}
            className={`max-w-[90%] whitespace-pre-line rounded-lg px-3 py-2 text-sm ${
              message.role === "user"
                ? "ml-auto bg-indigo-600 text-white"
                : "bg-slate-100 text-slate-800"
            }`}
          >
            {message.text}
          </div>
        ))}
        {busy && <div className="text-sm text-slate-500">Agents are working…</div>}
      </div>

      <form onSubmit={submit} className="flex gap-2 border-t border-slate-200 p-3">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="e.g. 12 question product feedback survey for our beta release"
          aria-label="Describe your questionnaire"
          className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
        />
        <button
          type="submit"
          disabled={busy || draft.trim().length < 3}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </section>
  );
}
