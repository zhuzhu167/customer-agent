<script setup lang="ts">
import type { SessionMode } from "@/types/events";

defineProps<{
  mode: SessionMode;
  disabled: boolean;
}>();

const emit = defineEmits<{
  modeChange: [mode: SessionMode];
}>();

const modes: SessionMode[] = ["normal", "demo", "debug"];
</script>

<template>
  <aside class="mode-switch" aria-label="会话模式">
    <div class="mode-switch__group" role="group" aria-label="展示模式">
      <button
        v-for="item in modes"
        :key="item"
        type="button"
        :class="{ 'is-active': mode === item }"
        :disabled="disabled"
        @click="emit('modeChange', item)"
      >
        {{ item }}
      </button>
    </div>
  </aside>
</template>
