<script setup lang="ts">
import { AlertTriangle, Mic, RefreshCw, X } from "@lucide/vue";

import type { VoiceCallState } from "@/types/events";

defineProps<{
  state: VoiceCallState;
  message?: string;
  recoverable?: boolean;
}>();

const emit = defineEmits<{
  retry: [];
  dismiss: [];
}>();
</script>

<template>
  <section v-if="state === 'error' || state === 'no_permission'" class="error-overlay" role="alert">
    <div class="error-overlay__content">
      <div class="error-overlay__icon">
        <Mic v-if="state === 'no_permission'" :size="28" />
        <AlertTriangle v-else :size="28" />
      </div>
      <h2>{{ state === "no_permission" ? "麦克风权限未开启" : "会话出现异常" }}</h2>
      <p>{{ message ?? "当前无法继续浏览器模拟通话，可以重新拨打。" }}</p>
      <div class="error-overlay__actions">
        <button v-if="recoverable !== false" type="button" class="error-overlay__primary" @click="emit('retry')">
          <RefreshCw :size="18" />
          重新拨打
        </button>
        <button type="button" class="error-overlay__ghost" @click="emit('dismiss')">
          <X :size="18" />
          稍后再试
        </button>
      </div>
    </div>
  </section>
</template>
