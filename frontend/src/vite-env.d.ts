/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SESSION_API_BASE_URL?: string;
  readonly VITE_SESSION_CREATE_PATH?: string;
  readonly VITE_AGENT_TURN_PATH?: string;
  readonly VITE_AGENT_AUDIO_TURN_PATH?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
