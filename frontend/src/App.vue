<script setup lang="ts">
import { computed } from "vue";
import { storeToRefs } from "pinia";

import CallControls from "@/components/CallControls.vue";
import CallStatus from "@/components/CallStatus.vue";
import ErrorOverlay from "@/components/ErrorOverlay.vue";
import ManualTurnInput from "@/components/ManualTurnInput.vue";
import EventRail from "@/components/EventRail.vue";
import ModeSwitch from "@/components/ModeSwitch.vue";
import TranscriptPeek from "@/components/TranscriptPeek.vue";
import VoiceField from "@/components/VoiceField.vue";
import { useCallStore } from "@/stores/callStore";

const callStore = useCallStore();
const {
  callState,
  mode,
  elapsedMs,
  events,
  latestVisibleTurns,
  partialTranscript,
  isPreparingVisible,
  error,
  canStart,
  canEnd,
  isMuted,
  speakerEnabled,
  inputLevel,
  outputLevel,
  latencyLevel,
  isActive,
  session,
  textTurnPending,
  audioTurnPending,
} = storeToRefs(callStore);

const showDebugRail = computed(() => mode.value === "debug");
const showIntroHint = computed(() => callState.value === "idle");
const showTranscriptPeek = computed(() => mode.value !== "normal" && isActive.value);
const showManualTurn = computed(
  () => mode.value !== "normal" && isActive.value && Boolean(session.value),
);
const showAudioTurn = computed(
  () => mode.value !== "normal" && isActive.value && Boolean(session.value),
);
</script>

<template>
  <main class="app-shell">
    <VoiceField
      :state="callState"
      :input-level="inputLevel"
      :output-level="outputLevel"
      :latency-level="latencyLevel"
    />

    <CallStatus
      :state="callState"
      :mode="mode"
      :elapsed-ms="elapsedMs"
      :event-count="events.length"
      :latency-level="latencyLevel"
      :muted="isMuted"
    />

    <ModeSwitch
      v-if="!isActive"
      :mode="mode"
      :disabled="isActive"
      @mode-change="callStore.setMode"
    />

    <section v-if="showIntroHint" class="hero-copy">
      <h1>{{ showIntroHint ? "点击拨打，开始浏览器模拟通话" : "AI 语音客服正在接听" }}</h1>
      <p>
        本次为浏览器内模拟通话。系统会使用麦克风进行实时语音交互，不会拨打真实电话。
      </p>
    </section>

    <TranscriptPeek
      v-if="showTranscriptPeek"
      :turns="latestVisibleTurns"
      :partial-transcript="partialTranscript"
      :preparing-visible="isPreparingVisible"
    />

    <ManualTurnInput
      v-if="showManualTurn"
      :disabled="callState === 'connecting'"
      :pending="textTurnPending"
      @submit="callStore.submitTextTurn"
    />

    <EventRail :events="events" :visible="showDebugRail" />

    <CallControls
      :state="callState"
      :can-start="canStart"
      :can-end="canEnd"
      :muted="isMuted"
      :speaker-enabled="speakerEnabled"
      :audio-turn-enabled="showAudioTurn"
      :audio-turn-pending="audioTurnPending"
      @start="callStore.startCall"
      @end="callStore.endCall"
      @retry="callStore.retryCall"
      @toggle-muted="callStore.toggleMuted"
      @toggle-speaker="callStore.toggleSpeaker"
      @record-turn="callStore.recordAudioTurn"
    />

    <ErrorOverlay
      :state="callState"
      :message="error?.message"
      :recoverable="error?.recoverable"
      @retry="callStore.retryCall"
      @dismiss="callStore.endCall"
    />
  </main>
</template>
