import { defineStore } from "pinia";
import { computed, ref, shallowRef } from "vue";

import { LiveKitCallClient } from "@/services/liveKitCall";
import { recordShortAudioTurn, requestMicrophoneProbe } from "@/services/microphone";
import {
  createVoiceSession,
  endVoiceSession,
  sendAgentAudioTurn,
  sendAgentTextTurn,
} from "@/services/sessionService";
import type {
  ConversationTurn,
  SessionMode,
  VoiceCallState,
  VoiceEvent,
} from "@/types/events";
import type { VoiceSessionConnection } from "@/types/session";

interface SessionErrorView {
  code: string;
  message: string;
  recoverable: boolean;
}

const ACTIVE_STATES: VoiceCallState[] = [
  "connecting",
  "listening",
  "thinking",
  "speaking",
  "interrupted",
];

export const useCallStore = defineStore("call", () => {
  const mode = ref<SessionMode>("demo");
  const callState = ref<VoiceCallState>("idle");
  const session = ref<VoiceSessionConnection | null>(null);
  const turns = ref<ConversationTurn[]>([]);
  const events = ref<VoiceEvent[]>([]);
  const partialTranscript = ref("");
  const startedAt = ref<number | null>(null);
  const endedAt = ref<number | null>(null);
  const elapsedMs = ref(0);
  const error = ref<SessionErrorView | null>(null);
  const isMuted = ref(false);
  const speakerEnabled = ref(true);
  const inputLevel = ref(0.16);
  const outputLevel = ref(0.12);
  const latencyLevel = ref<"good" | "warning" | "bad">("good");
  const textTurnPending = ref(false);
  const audioTurnPending = ref(false);
  const liveKitClient = shallowRef<LiveKitCallClient | null>(null);

  let elapsedTimer: number | undefined;
  let sequence = 1;

  const isActive = computed(() => ACTIVE_STATES.includes(callState.value));
  const canStart = computed(() => !isActive.value);
  const canEnd = computed(() => isActive.value || callState.value === "error");
  const visibleTurns = computed(() =>
    turns.value.filter((turn) => {
      if (turn.visibility === "internal") return mode.value === "debug";
      if (turn.visibility === "debug_only") return mode.value === "debug";
      if (turn.visibility === "demo_only") return mode.value !== "normal";
      return true;
    }),
  );
  const latestVisibleTurns = computed(() => visibleTurns.value.slice(-5));
  const isPreparingVisible = computed(() => mode.value === "demo" || mode.value === "debug");

  function setMode(nextMode: SessionMode): void {
    mode.value = nextMode;
  }

  async function startCall(): Promise<void> {
    if (!canStart.value) return;

    resetRuntime();
    callState.value = "permission_required";

    try {
      await requestMicrophoneProbe();
    } catch {
      const now = new Date().toISOString();
      callState.value = "no_permission";
      error.value = {
        code: "permission.denied",
        message: "需要麦克风权限才能开始浏览器内模拟通话。",
        recoverable: true,
      };
      appendEvent({
        type: "session.error",
        session_id: session.value?.sessionId ?? "local_permission_check",
        created_at: now,
        error_code: "permission.denied",
        message: error.value.message,
        recoverable: true,
      });
      return;
    }

    callState.value = "connecting";
    startElapsedClock();

    try {
      const connection = await createVoiceSession({
        mode: mode.value,
      });
      session.value = connection;
      appendEvent({
        type: "session.connected",
        session_id: connection.sessionId,
        turn_id: "session",
        sequence: sequence++,
        created_at: new Date().toISOString(),
        room_name: connection.roomName,
        identity: connection.identity,
      });

      const client = new LiveKitCallClient({
        onEvent: appendEvent,
        onRemoteAudioElement: (element) => {
          document.body.appendChild(element);
          element.muted = !speakerEnabled.value;
        },
        onError: (liveKitError) => {
          appendEvent({
            type: "session.error",
            session_id: connection.sessionId,
            created_at: new Date().toISOString(),
            error_code: "livekit.client_error",
            message: liveKitError.message,
            recoverable: true,
          });
        },
      });
      liveKitClient.value = client;
      await client.connect(connection);
      callState.value = "listening";
    } catch (startError) {
      const message = startError instanceof Error ? startError.message : "会话创建失败。";
      appendEvent({
        type: "session.error",
        session_id: session.value?.sessionId ?? "session_create_failed",
        created_at: new Date().toISOString(),
        error_code: "session.create_failed",
        message,
        recoverable: true,
      });
    }
  }

  async function endCall(reason: "user_ended" | "error" = "user_ended"): Promise<void> {
    await liveKitClient.value?.disconnect();
    liveKitClient.value = null;

    if (session.value && callState.value !== "ended") {
      await endVoiceSession({
        sessionId: session.value.sessionId,
        reason: reason === "user_ended" ? "user_requested" : reason,
      }).catch((endError) => {
        appendEvent({
          type: "session.error",
          session_id: session.value?.sessionId ?? "session_end_failed",
          created_at: new Date().toISOString(),
          error_code: "session.end_failed",
          message: endError instanceof Error ? endError.message : "结束后端会话失败。",
          recoverable: true,
        });
      });
      appendEvent({
        type: "session.closed",
        session_id: session.value.sessionId,
        turn_id: "session",
        sequence: sequence++,
        created_at: new Date().toISOString(),
        reason,
        summary: reason === "user_ended" ? "用户主动结束浏览器模拟通话。" : undefined,
      });
    }
    stopElapsedClock();
  }

  async function submitTextTurn(text: string): Promise<void> {
    if (!session.value || textTurnPending.value) return;

    textTurnPending.value = true;
    const turnSequence = sequence++;
    const startedAtIso = new Date().toISOString();

    appendEvent({
      type: "transcript.final",
      session_id: session.value.sessionId,
      turn_id: `manual_${turnSequence}`,
      sequence: turnSequence,
      created_at: startedAtIso,
      text,
      confidence: 1,
      is_final: true,
    });

    try {
      const result = await sendAgentTextTurn({
        sessionId: session.value.sessionId,
        text,
      });
      const answeredAtIso = new Date().toISOString();

      appendEvent({
        type: "agent.reply.preparing",
        session_id: result.session_id,
        turn_id: result.preparing_turn_id,
        sequence: sequence++,
        created_at: answeredAtIso,
        text: result.response_text,
        visibility: result.preparing_visible ? "demo_only" : "internal",
        latency_ms: Date.now() - new Date(startedAtIso).getTime(),
      });
      appendEvent({
        type: "agent.reply.answered",
        session_id: result.session_id,
        turn_id: result.answered_turn_id,
        sequence: sequence++,
        created_at: new Date().toISOString(),
        text: result.response_text,
        completed: true,
        playback_duration_ms: 0,
      });
    } catch (turnError) {
      appendEvent({
        type: "session.error",
        session_id: session.value.sessionId,
        created_at: new Date().toISOString(),
        error_code: "agent.turn_failed",
        message: turnError instanceof Error ? turnError.message : "后端 Agent turn 调用失败。",
        recoverable: true,
      });
    } finally {
      textTurnPending.value = false;
    }
  }

  async function recordAudioTurn(): Promise<void> {
    if (!session.value || audioTurnPending.value) return;

    audioTurnPending.value = true;
    callState.value = "listening";
    inputLevel.value = 0.78;

    try {
      const startedAtIso = new Date().toISOString();
      const audio = await recordShortAudioTurn();
      const result = await sendAgentAudioTurn({
        sessionId: session.value.sessionId,
        audio,
      });
      const turnSequence = sequence++;
      appendEvent({
        type: "transcript.final",
        session_id: result.session_id,
        turn_id: result.user_turn_id,
        sequence: turnSequence,
        created_at: startedAtIso,
        text: result.transcript_text,
        confidence: result.transcript_confidence,
        is_final: true,
      });
      appendEvent({
        type: "agent.reply.preparing",
        session_id: result.session_id,
        turn_id: result.preparing_turn_id,
        sequence: sequence++,
        created_at: new Date().toISOString(),
        text: result.response_text,
        visibility: result.preparing_visible ? "demo_only" : "internal",
        latency_ms: Date.now() - new Date(startedAtIso).getTime(),
      });
      appendEvent({
        type: "agent.reply.answered",
        session_id: result.session_id,
        turn_id: result.answered_turn_id,
        sequence: sequence++,
        created_at: new Date().toISOString(),
        text: result.response_text,
        completed: true,
        playback_duration_ms: 0,
      });
      playTtsAudio(result.tts_audio_base64, result.tts_content_type);
    } catch (turnError) {
      appendEvent({
        type: "session.error",
        session_id: session.value.sessionId,
        created_at: new Date().toISOString(),
        error_code: "agent.audio_turn_failed",
        message: turnError instanceof Error ? turnError.message : "后端 Agent audio turn 调用失败。",
        recoverable: true,
      });
    } finally {
      audioTurnPending.value = false;
      inputLevel.value = 0.2;
    }
  }

  function retryCall(): void {
    void endCall("user_ended").finally(() => {
      resetRuntime();
      void startCall();
    });
  }

  function toggleMuted(): void {
    isMuted.value = !isMuted.value;
    liveKitClient.value?.setMicrophoneMuted(isMuted.value);
  }

  function toggleSpeaker(): void {
    speakerEnabled.value = !speakerEnabled.value;
    liveKitClient.value?.setSpeakerEnabled(speakerEnabled.value);
  }

  function appendEvent(event: VoiceEvent): void {
    events.value.push(event);

    switch (event.type) {
      case "session.connected":
        callState.value = "listening";
        break;
      case "transcript.partial":
        partialTranscript.value = event.text;
        callState.value = "listening";
        inputLevel.value = 0.72;
        outputLevel.value = 0.16;
        break;
      case "transcript.final":
        partialTranscript.value = "";
        inputLevel.value = 0.28;
        callState.value = "thinking";
        addTurn({
          event,
          role: "user",
          visibility: "public",
          status: "final",
          text: event.text,
        });
        break;
      case "agent.reply.preparing":
        callState.value = "thinking";
        latencyLevel.value = event.latency_ms && event.latency_ms > 1200 ? "warning" : "good";
        addTurn({
          event,
          role: "agent_preparing",
          visibility: event.visibility,
          status: "speaking",
          text: event.text,
        });
        break;
      case "agent.reply.answered":
        callState.value = "speaking";
        outputLevel.value = 0.86;
        addTurn({
          event,
          role: "agent_answered",
          visibility: "public",
          status: event.completed ? "completed" : "failed",
          text: event.text,
        });
        scheduleLocal(() => {
          if (callState.value === "speaking") {
            outputLevel.value = 0.2;
            callState.value = "listening";
          }
        }, 900);
        break;
      case "agent.reply.interrupted":
        callState.value = "interrupted";
        outputLevel.value = 0.08;
        addTurn({
          event,
          role: "agent_answered",
          visibility: "public",
          status: "interrupted",
          text: event.played_text ?? "当前回答已被打断。",
        });
        scheduleLocal(() => {
          if (callState.value === "interrupted") callState.value = "listening";
        }, 420);
        break;
      case "agent.reply.interrupt_recovered":
        outputLevel.value = 0.12;
        inputLevel.value = 0.24;
        callState.value = "listening";
        addTurn({
          event,
          role: "agent_answered",
          visibility: "public",
          status: "completed",
          text: event.message,
        });
        break;
      case "session.error":
        callState.value = event.error_code === "permission.denied" ? "no_permission" : "error";
        outputLevel.value = 0.06;
        error.value = {
          code: event.error_code,
          message: event.message,
          recoverable: event.recoverable,
        };
        stopElapsedClock();
        break;
      case "session.closed":
        callState.value = "ended";
        endedAt.value = Date.now();
        partialTranscript.value = "";
        inputLevel.value = 0.08;
        outputLevel.value = 0.08;
        stopElapsedClock();
        break;
    }
  }

  function addTurn(params: {
    event: VoiceEvent;
    role: ConversationTurn["role"];
    visibility: ConversationTurn["visibility"];
    status: ConversationTurn["status"];
    text: string;
  }): void {
    turns.value.push({
      id: `${params.event.turn_id ?? "turn"}_${params.role}_${turns.value.length + 1}`,
      session_id: params.event.session_id,
      turn_index: turns.value.length + 1,
      role: params.role,
      text: params.text,
      visibility: params.visibility,
      status: params.status,
      created_at: params.event.created_at,
      completed_at:
        params.status === "completed" || params.status === "final"
          ? params.event.created_at
          : undefined,
    });
  }

  function resetRuntime(): void {
    turns.value = [];
    events.value = [];
    partialTranscript.value = "";
    session.value = null;
    error.value = null;
    startedAt.value = null;
    endedAt.value = null;
    elapsedMs.value = 0;
    inputLevel.value = 0.16;
    outputLevel.value = 0.12;
    latencyLevel.value = "good";
    textTurnPending.value = false;
    audioTurnPending.value = false;
    sequence = 1;
  }

  function playTtsAudio(base64Audio: string, contentType: string): void {
    if (!speakerEnabled.value) return;
    const audio = new Audio(`data:${contentType};base64,${base64Audio}`);
    void audio.play().catch(() => {
      if (!session.value) return;
      appendEvent({
        type: "session.error",
        session_id: session.value.sessionId,
        created_at: new Date().toISOString(),
        error_code: "browser.tts_playback_failed",
        message: "浏览器未能播放后端返回的 TTS 音频。",
        recoverable: true,
      });
    });
  }

  function startElapsedClock(): void {
    startedAt.value = Date.now();
    elapsedTimer = window.setInterval(() => {
      if (startedAt.value) elapsedMs.value = Date.now() - startedAt.value;
    }, 250);
  }

  function stopElapsedClock(): void {
    if (elapsedTimer) window.clearInterval(elapsedTimer);
    elapsedTimer = undefined;
  }

  function scheduleLocal(callback: () => void, delay: number): void {
    window.setTimeout(callback, delay);
  }

  return {
    mode,
    callState,
    session,
    turns,
    events,
    partialTranscript,
    startedAt,
    endedAt,
    elapsedMs,
    error,
    isMuted,
    speakerEnabled,
    inputLevel,
    outputLevel,
    latencyLevel,
    textTurnPending,
    audioTurnPending,
    isActive,
    canStart,
    canEnd,
    visibleTurns,
    latestVisibleTurns,
    isPreparingVisible,
    setMode,
    startCall,
    endCall,
    retryCall,
    toggleMuted,
    toggleSpeaker,
    submitTextTurn,
    recordAudioTurn,
  };
});
