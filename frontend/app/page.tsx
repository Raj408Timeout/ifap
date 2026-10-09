"use client";

import { useCallback, useState } from "react";
import { ChatPanel, type ChatMessage } from "@/components/ChatPanel";
import { LlmToggle } from "@/components/LlmToggle";
import { QuestionnaireEditor } from "@/components/QuestionnaireEditor";
import { WorkflowPicker } from "@/components/WorkflowPicker";
import { api, ApiError } from "@/lib/api";
import type { AgentTrace, GenerateResponse, Questionnaire, ValidationSummary } from "@/lib/types";

interface Workspace {
  questionnaire: Questionnaire;
  validation: ValidationSummary;
  trace: AgentTrace[];
  dirty: boolean;
}

/** Which model answered each LLM-backed step - or why a step ran without one. */
function modelsUsed(trace: AgentTrace[]): string {
  const llmSteps = trace.filter((step) => step.models.length > 0);
  const fallbacks = trace.filter((step) => step.strategy === "heuristic");
  const used = llmSteps.map((step) => `${step.agent} → ${step.models.join(", ")}`);
  const skipped = fallbacks.map((step) => `${step.agent} → heuristic`);
  if (used.length === 0) return "No AI model was used - every step ran on IFAP's heuristics.";
  return `Models: ${[...used, ...skipped].join(" · ")}`;
}

function summarise(result: GenerateResponse): string {
  const { intent, questionnaire, source_count: sources } = result;
  const confidence = Math.round(intent.confidence * 100);
  return [
    `I understood this as a ${intent.survey_type.replaceAll("_", " ")} (${confidence}% confidence) and ran the "${result.workflow}" workflow.`,
    `Retrieved ${sources} candidate templates and built "${questionnaire.title}" with ${questionnaire.questions.length} questions.`,
    modelsUsed(result.trace),
    "Review it on the right - edit, reorder or remove questions, then save and publish.",
  ].join("\n");
}

const errorText = (error: unknown) =>
  error instanceof ApiError ? `${error.status}: ${error.message}` : "The API is unreachable.";

export default function Home() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [busy, setBusy] = useState(false);
  const [workflow, setWorkflow] = useState<string | null>(null);
  const [llmRefresh, setLlmRefresh] = useState(0); // bump to re-read the model chain status
  const chooseWorkflow = useCallback((name: string) => setWorkflow(name), []);

  const say = (message: ChatMessage) => setMessages((current) => [...current, message]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      say({ role: "assistant", text: `Something went wrong - ${errorText(error)}` });
    } finally {
      setBusy(false);
    }
  };

  const generate = (text: string) => {
    say({ role: "user", text });
    void run(async () => {
      const result = await api.generate(text, workflow ?? undefined);
      setWorkspace({ ...result, dirty: false });
      say({ role: "assistant", text: summarise(result) });
      setLlmRefresh((n) => n + 1); // the active model may have changed (quota, cooldown)
    });
  };

  const save = () =>
    workspace &&
    void run(async () => {
      const { id, title, description, questions } = workspace.questionnaire;
      const result = await api.revise(id, title, description, questions);
      setWorkspace({ ...workspace, ...result, dirty: false });
      say({ role: "assistant", text: `Saved version ${result.questionnaire.version}.` });
    });

  const publish = () =>
    workspace &&
    void run(async () => {
      const questionnaire = await api.publish(workspace.questionnaire.id);
      setWorkspace({ ...workspace, questionnaire });
      say({ role: "assistant", text: `Published "${questionnaire.title}". It is now ready to collect responses.` });
    });

  return (
    <main className="mx-auto flex min-h-screen max-w-7xl flex-col gap-4 p-4 lg:p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Intelligent Feedback &amp; Assessment Platform</h1>
          <p className="text-sm text-slate-500">
            Intent → Template Retrieval (RAG) → Questionnaire Builder → Validation
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <WorkflowPicker value={workflow} onChange={chooseWorkflow} />
          <LlmToggle refresh={llmRefresh} />
        </div>
      </header>
      <div className="grid flex-1 gap-4 lg:grid-cols-[380px_1fr]">
        <div className="h-[75vh] lg:sticky lg:top-6">
          <ChatPanel messages={messages} busy={busy} onSend={generate} />
        </div>
        {workspace ? (
          <QuestionnaireEditor
            questionnaire={workspace.questionnaire}
            validation={workspace.validation}
            trace={workspace.trace}
            dirty={workspace.dirty}
            busy={busy}
            onChange={(questionnaire) => setWorkspace({ ...workspace, questionnaire, dirty: true })}
            onSave={save}
            onPublish={publish}
          />
        ) : (
          <div className="flex items-center justify-center rounded-xl border border-dashed border-slate-300 p-10 text-center text-slate-400">
            Your generated questionnaire will appear here.
          </div>
        )}
      </div>
    </main>
  );
}
