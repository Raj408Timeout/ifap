// Mirrors the backend API contracts (see docs/08-api-specification.md).

export type AnswerType =
  | "boolean"
  | "multiple_choice"
  | "single_choice"
  | "rich_text"
  | "numeric"
  | "date"
  | "file_upload";

export interface Choice {
  value: string;
  label: string;
}

export interface Validation {
  kind: string;
  value: string | number | boolean | string[] | null;
  message: string | null;
}

export interface DependencyRule {
  depends_on: string;
  operator: string;
  value: string | number | boolean;
  action: "show" | "skip";
}

export interface Question {
  id: string;
  label: string;
  category: string;
  description: string;
  answer_type: AnswerType;
  choices: Choice[];
  validations: Validation[];
  dependency_rules: DependencyRule[];
  business_tags: string[];
}

export interface Questionnaire {
  id: string;
  title: string;
  description: string;
  survey_type: string;
  status: "draft" | "published" | "archived";
  version: number;
  questions: Question[];
  source_template_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface ValidationIssue {
  code: string;
  message: string;
  severity: "error" | "warning";
  question_id: string | null;
}

export interface ValidationSummary {
  is_valid: boolean;
  issues: ValidationIssue[];
}

export interface AgentTrace {
  agent: string;
  version: string;
  status: "succeeded" | "failed";
  attempts: number;
  duration_ms: number;
  strategy: string;
  note: string;
  /** LLM models that answered this step; empty = no LLM (heuristic or deterministic). */
  models: string[];
}

export interface BusinessIntent {
  survey_type: string;
  business_function: string | null;
  industry: string | null;
  question_count: number;
  keywords: string[];
  confidence: number;
}

export interface GenerateResponse {
  questionnaire: Questionnaire;
  intent: BusinessIntent;
  validation: ValidationSummary;
  trace: AgentTrace[];
  source_count: number;
  workflow: string;
}

export interface ReviseResponse {
  questionnaire: Questionnaire;
  validation: ValidationSummary;
}

export interface AgentsInfo {
  workflows: Record<string, string[]>;
  default_workflow: string;
  llm_enabled: boolean;
}

export interface ModelState {
  name: string;
  available: boolean;
  reason: string | null;
  available_in_seconds: number | null;
}

export interface LLMStatus {
  provider: string;
  /** Model the next call will use (first available in the chain). */
  model: string | null;
  configured: boolean;
  enabled: boolean;
  models: ModelState[];
}
