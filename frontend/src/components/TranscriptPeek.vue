<script setup lang="ts">
import { computed } from "vue";

import type { ConversationTurn } from "@/types/events";

const props = defineProps<{
  turns: ConversationTurn[];
  partialTranscript: string;
  preparingVisible: boolean;
}>();

const latestText = computed(() => {
  if (props.partialTranscript) {
    return {
      label: "用户说话",
      text: props.partialTranscript,
      tone: "user",
    };
  }

  const latest = [...props.turns].reverse().find((turn) => {
    if (turn.role === "agent_preparing") return props.preparingVisible;
    return true;
  });

  if (!latest) return null;

  const labelMap: Record<ConversationTurn["role"], string> = {
    user: "用户说话",
    agent_preparing: "客服准备回答",
    agent_answered: "客服已回答",
  };

  return {
    label: labelMap[latest.role],
    text: summarize(latest.text, latest.role),
    tone: latest.role,
  };
});

function summarize(text: string, role: ConversationTurn["role"]): string {
  const limit = role === "agent_preparing" ? 52 : 64;
  return text.length > limit ? `${text.slice(0, limit)}...` : text;
}
</script>

<template>
  <section v-if="latestText" class="transcript-peek" :class="`transcript-peek--${latestText.tone}`" aria-live="polite">
    <span class="transcript-peek__label">{{ latestText.label }}</span>
    <p>{{ latestText.text }}</p>
  </section>
</template>
