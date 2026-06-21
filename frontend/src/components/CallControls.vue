<script setup lang="ts">
import {
  Mic,
  MicOff,
  Phone,
  PhoneOff,
  Radio,
  RefreshCw,
  Volume2,
  VolumeX,
} from "@lucide/vue";

import type { VoiceCallState } from "@/types/events";

defineProps<{
  state: VoiceCallState;
  canStart: boolean;
  canEnd: boolean;
  muted: boolean;
  speakerEnabled: boolean;
  audioTurnEnabled: boolean;
  audioTurnPending: boolean;
}>();

const emit = defineEmits<{
  start: [];
  end: [];
  retry: [];
  toggleMuted: [];
  toggleSpeaker: [];
  recordTurn: [];
}>();
</script>

<template>
  <nav class="call-controls" aria-label="通话控制">
    <button
      class="call-controls__button"
      type="button"
      :disabled="!canEnd"
      title="麦克风"
      aria-label="切换麦克风"
      @click="emit('toggleMuted')"
    >
      <MicOff v-if="muted" :size="21" />
      <Mic v-else :size="21" />
      <span v-if="muted" class="call-controls__warning-dot"></span>
    </button>

    <button
      v-if="state === 'idle' || state === 'ended' || state === 'no_permission'"
      class="call-controls__button call-controls__button--primary"
      type="button"
      :disabled="!canStart"
      title="拨打电话"
      aria-label="拨打电话"
      @click="emit('start')"
    >
      <Phone :size="26" />
    </button>

    <button
      v-else-if="state === 'error'"
      class="call-controls__button call-controls__button--primary"
      type="button"
      title="重新拨打"
      aria-label="重新拨打"
      @click="emit('retry')"
    >
      <RefreshCw :size="25" />
    </button>

    <button
      v-else
      class="call-controls__button call-controls__button--danger"
      type="button"
      :disabled="!canEnd"
      title="结束通话"
      aria-label="结束通话"
      @click="emit('end')"
    >
      <PhoneOff :size="27" />
    </button>

    <button
      class="call-controls__button"
      type="button"
      :disabled="!canEnd"
      title="扬声器"
      aria-label="切换扬声器"
      @click="emit('toggleSpeaker')"
    >
      <Volume2 v-if="speakerEnabled" :size="21" />
      <VolumeX v-else :size="21" />
    </button>

    <button
      v-if="audioTurnEnabled"
      class="call-controls__button call-controls__button--small"
      type="button"
      :disabled="!canEnd || audioTurnPending"
      title="录制一轮用户语音"
      aria-label="录制一轮用户语音"
      @click="emit('recordTurn')"
    >
      <Radio :size="18" />
      <span v-if="audioTurnPending" class="call-controls__warning-dot"></span>
    </button>
  </nav>
</template>
