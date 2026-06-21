export type SessionMode = "normal" | "demo" | "debug";

export type VoiceCallState =
  | "idle"
  | "permission_required"
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "interrupted"
  | "ended"
  | "error"
  | "offline"
  | "no_permission";

export type VoiceEventType =
  | "session.connected"
  | "transcript.partial"
  | "transcript.final"
  | "agent.reply.preparing"
  | "agent.reply.answered"
  | "agent.reply.interrupted"
  | "agent.reply.interrupt_recovered"
  | "session.error"
  | "session.closed";

export interface VoiceEventBase {
  type: VoiceEventType;
  session_id: string;
  turn_id?: string;
  sequence?: number;
  created_at: string;
}

export interface TranscriptEvent extends VoiceEventBase {
  type: "transcript.partial" | "transcript.final";
  text: string;
  confidence?: number;
  is_final?: boolean;
}

export interface AgentReplyPreparingEvent extends VoiceEventBase {
  type: "agent.reply.preparing";
  text: string;
  visibility: "demo_only" | "debug_only" | "internal";
  latency_ms?: number;
}

export interface AgentReplyAnsweredEvent extends VoiceEventBase {
  type: "agent.reply.answered";
  text: string;
  playback_duration_ms?: number;
  completed: boolean;
}

export interface AgentReplyInterruptedEvent extends VoiceEventBase {
  type: "agent.reply.interrupted";
  interruption_id?: string;
  interrupted_at_ms?: number;
  played_text?: string;
  reason: "barge_in" | "manual_stop" | "network" | "unknown";
}

export interface AgentReplyInterruptRecoveredEvent extends VoiceEventBase {
  type: "agent.reply.interrupt_recovered";
  interruption_id: string;
  message: string;
  reason: "no_follow_up_transcript" | "asr_timeout" | "unknown";
}

export interface SessionConnectedEvent extends VoiceEventBase {
  type: "session.connected";
  room_name?: string;
  identity?: string;
}

export interface SessionErrorEvent extends VoiceEventBase {
  type: "session.error";
  error_code: string;
  message: string;
  recoverable: boolean;
}

export interface SessionClosedEvent extends VoiceEventBase {
  type: "session.closed";
  reason: "user_ended" | "error" | "remote_closed";
  summary?: string;
}

export type VoiceEvent =
  | SessionConnectedEvent
  | TranscriptEvent
  | AgentReplyPreparingEvent
  | AgentReplyAnsweredEvent
  | AgentReplyInterruptedEvent
  | AgentReplyInterruptRecoveredEvent
  | SessionErrorEvent
  | SessionClosedEvent;

export type ConversationTurnRole = "user" | "agent_preparing" | "agent_answered";

export type ConversationTurnStatus =
  | "partial"
  | "final"
  | "speaking"
  | "completed"
  | "interrupted"
  | "failed";

export interface ConversationTurn {
  id: string;
  session_id: string;
  turn_index: number;
  role: ConversationTurnRole;
  text: string;
  visibility: "public" | "demo_only" | "debug_only" | "internal";
  status: ConversationTurnStatus;
  created_at: string;
  completed_at?: string;
}

export const REQUIRED_FRONTEND_EVENT_TYPES: readonly VoiceEventType[] = [
  "transcript.partial",
  "transcript.final",
  "agent.reply.preparing",
  "agent.reply.answered",
  "agent.reply.interrupted",
  "agent.reply.interrupt_recovered",
  "session.error",
  "session.closed",
];

export function isVoiceEvent(value: unknown): value is VoiceEvent {
  if (!value || typeof value !== "object") return false;
  const candidate = value as { type?: unknown; session_id?: unknown; created_at?: unknown };
  return (
    typeof candidate.type === "string" &&
    typeof candidate.session_id === "string" &&
    typeof candidate.created_at === "string" &&
    [
      "session.connected",
      ...REQUIRED_FRONTEND_EVENT_TYPES,
    ].includes(candidate.type as VoiceEventType)
  );
}
