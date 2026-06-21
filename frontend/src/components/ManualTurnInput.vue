<script setup lang="ts">
import { Send } from "@lucide/vue";
import { ref } from "vue";

defineProps<{
  disabled: boolean;
  pending: boolean;
}>();

const emit = defineEmits<{
  submit: [text: string];
}>();

const text = ref("");

function submit(): void {
  const value = text.value.trim();
  if (!value) return;
  emit("submit", value);
  text.value = "";
}
</script>

<template>
  <form class="manual-turn" @submit.prevent="submit">
    <input
      v-model="text"
      type="text"
      :disabled="disabled || pending"
      placeholder="输入一轮用户话语，调用后端真实 Agent"
      aria-label="用户话语"
    />
    <button type="submit" :disabled="disabled || pending || !text.trim()" aria-label="发送">
      <Send :size="16" />
    </button>
  </form>
</template>
