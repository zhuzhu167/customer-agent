import type {
  AgentTextTurnResponse,
  AgentAudioTurnResponse,
  RawSessionCreateResponse,
  SessionCreateRequest,
  VoiceSessionConnection,
} from "@/types/session";
import type { SessionMode } from "@/types/events";

const DEFAULT_SESSION_CREATE_PATH = "/api/v1/sessions";
const DEFAULT_AGENT_TURN_PATH = "/api/v1/agent/turn";
const DEFAULT_AGENT_AUDIO_TURN_PATH = "/api/v1/agent/audio-turn";

export async function createVoiceSession(params: {
  mode: SessionMode;
}): Promise<VoiceSessionConnection> {
  const apiBaseUrl = import.meta.env.VITE_SESSION_API_BASE_URL?.replace(/\/$/, "");
  if (!apiBaseUrl) {
    throw new Error("缺少 VITE_SESSION_API_BASE_URL，生产路径不能自动回退到 mock 会话。");
  }

  const request: SessionCreateRequest = {
    mode: params.mode,
    client_capabilities: {
      livekit: true,
      microphone: true,
      data_channel: true,
      preparing_visibility: "demo_debug_only",
    },
  };

  const response = await fetch(
    `${apiBaseUrl}${import.meta.env.VITE_SESSION_CREATE_PATH ?? DEFAULT_SESSION_CREATE_PATH}`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    },
  );

  if (!response.ok) {
    throw new Error(`session.create failed: ${response.status}`);
  }

  return normalizeSessionResponse((await response.json()) as RawSessionCreateResponse, params.mode);
}

export async function endVoiceSession(params: {
  sessionId: string;
  reason?: string;
}): Promise<void> {
  const apiBaseUrl = import.meta.env.VITE_SESSION_API_BASE_URL?.replace(/\/$/, "");
  if (!apiBaseUrl) return;

  const response = await fetch(`${apiBaseUrl}/api/v1/sessions/${params.sessionId}/end`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ reason: params.reason ?? "user_requested" }),
  });

  if (!response.ok) {
    throw new Error(`session.end failed: ${response.status}`);
  }
}

export async function sendAgentTextTurn(params: {
  sessionId: string;
  text: string;
}): Promise<AgentTextTurnResponse> {
  const apiBaseUrl = import.meta.env.VITE_SESSION_API_BASE_URL?.replace(/\/$/, "");
  if (!apiBaseUrl) {
    throw new Error("缺少 VITE_SESSION_API_BASE_URL，无法调用后端 Agent turn。");
  }

  const response = await fetch(`${apiBaseUrl}${import.meta.env.VITE_AGENT_TURN_PATH ?? DEFAULT_AGENT_TURN_PATH}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: params.sessionId,
      text: params.text,
    }),
  });

  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    const message =
      detail && typeof detail === "object" && "message" in detail
        ? String(detail.message)
        : `agent.turn failed: ${response.status}`;
    throw new Error(message);
  }

  return (await response.json()) as AgentTextTurnResponse;
}

export async function sendAgentAudioTurn(params: {
  sessionId: string;
  audio: Blob;
}): Promise<AgentAudioTurnResponse> {
  const apiBaseUrl = import.meta.env.VITE_SESSION_API_BASE_URL?.replace(/\/$/, "");
  if (!apiBaseUrl) {
    throw new Error("缺少 VITE_SESSION_API_BASE_URL，无法调用后端 Agent audio turn。");
  }

  const formData = new FormData();
  formData.append("session_id", params.sessionId);
  formData.append("audio", params.audio, "browser-turn.wav");

  const response = await fetch(
    `${apiBaseUrl}${import.meta.env.VITE_AGENT_AUDIO_TURN_PATH ?? DEFAULT_AGENT_AUDIO_TURN_PATH}`,
    {
      method: "POST",
      body: formData,
    },
  );

  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    const message =
      detail && typeof detail === "object" && "message" in detail
        ? String(detail.message)
        : `agent.audio_turn failed: ${response.status}`;
    throw new Error(message);
  }

  return (await response.json()) as AgentAudioTurnResponse;
}

function normalizeSessionResponse(
  raw: RawSessionCreateResponse,
  fallbackMode: SessionMode,
): VoiceSessionConnection {
  const sessionId = raw.session_id ?? raw.sessionId;
  const livekit = raw.livekit;
  const roomName = livekit?.room_name ?? livekit?.roomName ?? raw.room_name ?? raw.roomName;
  const token = livekit?.token ?? raw.token ?? raw.livekit_token;
  const url = livekit?.url ?? raw.url ?? raw.livekit_url;

  if (!sessionId || !roomName || !token || !url) {
    throw new Error("session.create response missing LiveKit connection fields");
  }

  return {
    sessionId,
    roomName,
    token,
    url,
    mode: raw.mode ?? fallbackMode,
    identity: livekit?.user_identity ?? livekit?.userIdentity ?? raw.identity,
  };
}
