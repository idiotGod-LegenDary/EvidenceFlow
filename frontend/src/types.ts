export type Strategy = "direct" | "single" | "multi" | "agent" | "refuse";

export interface Citation {
  document_id: string;
  title: string;
  chunk_id: string;
  section: string | null;
  knowledge_version?: string;
  content_sha256?: string;
}

export interface ActionProposal {
  action_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  risk: string;
  summary: string;
  case_id?: string | null;
  knowledge_version?: string;
  expires_at?: string | null;
}

export interface ChatResponse {
  answer: string;
  session_id: string;
  trace_id: string;
  intent: string | null;
  complexity: string | null;
  strategy: Strategy | null;
  citations: Citation[];
  pending_action: ActionProposal | null;
  refused: boolean;
  error_code: string | null;
  latency: { total_ms: number };
  case_id?: string | null;
  case_status?: string | null;
}

export type CaseStatus = "pending_review" | "executing" | "completed" | "needs_review" | "failed" | "cancelled";

export interface CaseSummary {
  case_id: string;
  session_id: string;
  title: string;
  order_id: string;
  status: CaseStatus;
  notice: string;
  attempts: number;
  created_at: string;
  updated_at: string;
}

export interface CaseDetail extends CaseSummary {
  request_text: string;
  action: ActionProposal;
  knowledge_version: string;
  order_before: Record<string, unknown>;
  evidence: { chunk_id: string; title: string; content: string; content_sha256: string; knowledge_version: string }[];
  result: Record<string, unknown>;
  verification: Record<string, boolean>;
  events: { kind: string; message: string; detail: Record<string, unknown>; created_at: string }[];
  messages: { role: "user" | "assistant" | "system" | "tool"; content: string }[];
}

export interface ChatRequest {
  message: string;
  session_id?: string;
  user_id: string;
  confirm_action_id?: string;
}

export interface Capabilities {
  database: string;
  cache: string;
  vector_store: string;
  retriever: string;
  reranker: string;
  llm: string;
  degraded: boolean;
}

export type StreamEvent =
  | { event: "started"; trace_id: string }
  | { event: "node"; node: string; intent?: string; strategy?: Strategy }
  | { event: "token"; text: string }
  | { event: "answer"; data: ChatResponse }
  | { event: "done"; trace_id: string };

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  state: "complete" | "streaming" | "error" | "stopped";
  citations?: Citation[];
  pendingAction?: ActionProposal | null;
  strategy?: Strategy | null;
  traceId?: string;
  latencyMs?: number;
}

export interface Conversation {
  id: string;
  title: string;
  updatedAt: number;
  sessionId?: string;
  messages: Message[];
}
