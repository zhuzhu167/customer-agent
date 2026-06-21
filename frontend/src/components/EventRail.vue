<script setup lang="ts">
import { computed } from "vue";

import type { VoiceEvent } from "@/types/events";

const props = defineProps<{
  events: VoiceEvent[];
  visible: boolean;
}>();

const latestEvents = computed(() => props.events.slice(-8).reverse());
</script>

<template>
  <aside v-if="visible" class="event-rail" aria-label="调试事件">
    <header>
      <span>事件协议</span>
      <strong>{{ events.length }}</strong>
    </header>
    <ol>
      <li v-for="event in latestEvents" :key="`${event.type}-${event.sequence ?? event.created_at}`">
        <span>{{ event.type }}</span>
        <time>{{ new Date(event.created_at).toLocaleTimeString("zh-CN", { hour12: false }) }}</time>
      </li>
    </ol>
  </aside>
</template>
