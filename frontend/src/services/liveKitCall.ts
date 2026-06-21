import {
  createLocalAudioTrack,
  Room,
  RoomEvent,
  Track,
  type LocalAudioTrack,
} from "livekit-client";

import { isVoiceEvent, type VoiceEvent } from "@/types/events";
import type { VoiceSessionConnection } from "@/types/session";

export interface LiveKitCallClientOptions {
  onEvent: (event: VoiceEvent) => void;
  onRemoteAudioElement?: (element: HTMLMediaElement) => void;
  onError?: (error: Error) => void;
}

export class LiveKitCallClient {
  private room: Room | null = null;
  private localAudioTrack: LocalAudioTrack | null = null;
  private readonly remoteAudioElements = new Set<HTMLMediaElement>();
  private readonly decoder = new TextDecoder();

  constructor(private readonly options: LiveKitCallClientOptions) {}

  async connect(session: VoiceSessionConnection): Promise<void> {
    const room = new Room({
      adaptiveStream: true,
      dynacast: true,
    });
    this.room = room;

    room.on(RoomEvent.DataReceived, (payload) => {
      this.handleDataPacket(payload);
    });

    room.on(RoomEvent.TrackSubscribed, (track) => {
      if (track.kind !== Track.Kind.Audio) return;
      const element = track.attach();
      element.autoplay = true;
      element.dataset.livekitAgentAudio = "true";
      this.remoteAudioElements.add(element);
      this.options.onRemoteAudioElement?.(element);
    });

    room.on(RoomEvent.TrackUnsubscribed, (track) => {
      track.detach().forEach((element) => {
        element.remove();
        this.remoteAudioElements.delete(element as HTMLMediaElement);
      });
    });

    room.on(RoomEvent.Disconnected, () => {
      this.teardownRemoteAudio();
    });

    try {
      await room.connect(session.url, session.token);
      this.localAudioTrack = await createLocalAudioTrack({
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      });
      await room.localParticipant.publishTrack(this.localAudioTrack, {
        name: "browser-microphone",
        source: Track.Source.Microphone,
      });
    } catch (error) {
      this.options.onError?.(error instanceof Error ? error : new Error(String(error)));
      await this.disconnect();
      throw error;
    }
  }

  setMicrophoneMuted(muted: boolean): void {
    if (!this.localAudioTrack) return;
    if (muted) {
      this.localAudioTrack.mute();
    } else {
      this.localAudioTrack.unmute();
    }
  }

  setSpeakerEnabled(enabled: boolean): void {
    this.remoteAudioElements.forEach((element) => {
      element.muted = !enabled;
    });
  }

  async disconnect(): Promise<void> {
    this.localAudioTrack?.stop();
    this.localAudioTrack = null;
    this.teardownRemoteAudio();
    this.room?.disconnect();
    this.room = null;
  }

  private handleDataPacket(payload: Uint8Array): void {
    try {
      const decoded = JSON.parse(this.decoder.decode(payload)) as unknown;
      if (isVoiceEvent(decoded)) {
        this.options.onEvent(decoded);
      }
    } catch (error) {
      this.options.onError?.(error instanceof Error ? error : new Error(String(error)));
    }
  }

  private teardownRemoteAudio(): void {
    this.remoteAudioElements.forEach((element) => element.remove());
    this.remoteAudioElements.clear();
  }
}
