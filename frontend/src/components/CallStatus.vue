<script setup lang="ts">
import { computed } from "vue";
import { AudioLines, Mic, MicOff, SignalHigh } from "@lucide/vue";

import type { SessionMode, VoiceCallState } from "@/types/events";

const props = defineProps<{
  state: VoiceCallState;
  mode: SessionMode;
  elapsedMs: number;
  eventCount: number;
  latencyLevel: "good" | "warning" | "bad";
  muted: boolean;
}>();

const elapsed = computed(() => {
  const totalSeconds = Math.floor(props.elapsedMs / 1000);
  const minutes = String(Math.floor(totalSeconds / 60)).padStart(2, "0");
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
});

const stateLabel = computed(() => {
  const labels: Record<VoiceCallState, string> = {
    idle: "点击拨打，开始模拟通话",
    permission_required: "等待麦克风授权",
    connecting: "正在连接 Agent...",
    listening: "正在聆听",
    thinking: "客服正在组织回答",
    speaking: "语音响应中",
    interrupted: "已停止当前播报",
    ended: "通话已结束",
    error: "连接异常 · 可重新拨打",
    offline: "网络连接异常",
    no_permission: "麦克风权限未开启",
  };
  return labels[props.state];
});

const centerStatus = computed(() => {
  if (props.state === "ended" && props.elapsedMs > 0) return `通话已结束 · ${elapsed.value}`;
  if (props.elapsedMs > 0) return elapsed.value;
  return stateLabel.value;
});

const modeLabel = computed(() => {
  if (props.mode === "debug") return "debug 模式";
  if (props.mode === "demo") return "demo 模式";
  return "normal 模式";
});

const latencyLabel = computed(() => {
  if (props.latencyLevel === "bad") return "高延迟";
  if (props.latencyLevel === "warning") return "轻微波动";
  return "低延迟";
});

const rightStatus = computed(() => {
  const micText =
    props.state === "idle" || props.state === "permission_required"
      ? "麦克风待授权"
      : props.state === "ended"
        ? "麦克风已释放"
      : props.muted
        ? "麦克风关闭"
        : "麦克风开启";
  const debugText = props.mode === "debug" ? ` · backend · ${props.eventCount} events` : "";
  return `${micText} · ${latencyLabel.value}${debugText}`;
});
</script>

<template>
  <header class="call-status">
    <div class="call-status__brand">
      <AudioLines :size="22" stroke-width="1.8" />
      <span>AI 语音客服</span>
    </div>
    <div class="call-status__center" aria-live="polite">
      <span>浏览器模拟通话</span>
      <span class="call-status__divider">·</span>
      <span class="call-status__time">{{ centerStatus }}</span>
    </div>
    <div class="call-status__meta">
      <MicOff v-if="muted" :size="18" />
      <Mic v-else :size="18" />
      <span>{{ rightStatus }}</span>
      <SignalHigh :size="19" />
      <span v-if="mode !== 'normal'" class="call-status__debug">{{ modeLabel }}</span>
    </div>
  </header>
</template>
