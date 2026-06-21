import type { SessionMode } from "./events";

export interface SessionCreateRequest {
  mode: SessionMode;
  client_capabilities: {
    livekit: boolean;
    microphone: boolean;
    data_channel: boolean;
    preparing_visibility: "demo_debug_only";
  };
}

export interface VoiceSessionConnection {
  sessionId: string;
  roomName: string;
  token: string;
  url: string;
  mode: SessionMode;
  identity?: string;
}

export interface RawLiveKitJoinInfo {
  url?: string;
  room_name?: string;
  roomName?: string;
  token?: string;
  user_identity?: string;
  userIdentity?: string;
  worker_identity?: string;
  workerIdentity?: string;
  token_expires_in?: number;
  tokenExpiresIn?: number;
}

export interface RawSessionCreateResponse {
  session_id?: string;
  sessionId?: string;
  room_name?: string;
  roomName?: string;
  token?: string;
  livekit_token?: string;
  url?: string;
  livekit_url?: string;
  livekit?: RawLiveKitJoinInfo;
  mode?: SessionMode;
  identity?: string;
}

export interface AgentTextTurnResponse {
  session_id: string;
  user_turn_id: string;
  preparing_turn_id: string;
  answered_turn_id: string;
  response_text: string;
  preparing_visible: boolean;
  tts_provider: string;
  tts_content_type: string;
  tts_audio_bytes: number;
}

export interface AgentAudioTurnResponse extends AgentTextTurnResponse {
  transcript_text: string;
  transcript_confidence?: number;
  tts_audio_base64: string;
}
