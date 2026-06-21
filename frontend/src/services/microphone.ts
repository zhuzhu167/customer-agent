export async function requestMicrophoneProbe(): Promise<void> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("browser.media_devices_unavailable");
  }

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });

  stream.getTracks().forEach((track) => track.stop());
}

export async function recordShortAudioTurn(durationMs = 3600): Promise<Blob> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("browser.audio_recording_unavailable");
  }

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });

  const audioContext = new AudioContext();
  const source = audioContext.createMediaStreamSource(stream);
  const processor = audioContext.createScriptProcessor(4096, 1, 1);
  const chunks: Float32Array[] = [];
  const inputSampleRate = audioContext.sampleRate;

  return new Promise<Blob>((resolve, reject) => {
    const cleanup = async () => {
      source.disconnect();
      processor.disconnect();
      stream.getTracks().forEach((track) => track.stop());
      await audioContext.close().catch(() => undefined);
    };

    processor.onaudioprocess = (event) => {
      chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
    };

    source.connect(processor);
    processor.connect(audioContext.destination);

    window.setTimeout(() => {
      cleanup()
        .then(() => resolve(encodeWav(resampleTo16k(mergeFloat32(chunks), inputSampleRate), 16000)))
        .catch(() => reject(new Error("browser.audio_recording_failed")));
    }, durationMs);
  });
}

function mergeFloat32(chunks: Float32Array[]): Float32Array {
  const totalLength = chunks.reduce((sum, item) => sum + item.length, 0);
  const merged = new Float32Array(totalLength);
  let offset = 0;
  chunks.forEach((item) => {
    merged.set(item, offset);
    offset += item.length;
  });
  return merged;
}

function resampleTo16k(input: Float32Array, inputSampleRate: number): Float32Array {
  if (inputSampleRate === 16000) return input;
  const ratio = inputSampleRate / 16000;
  const outputLength = Math.floor(input.length / ratio);
  const output = new Float32Array(outputLength);
  for (let i = 0; i < outputLength; i += 1) {
    const sourceIndex = i * ratio;
    const before = Math.floor(sourceIndex);
    const after = Math.min(before + 1, input.length - 1);
    const weight = sourceIndex - before;
    output[i] = (input[before] ?? 0) * (1 - weight) + (input[after] ?? 0) * weight;
  }
  return output;
}

function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const bytesPerSample = 2;
  const buffer = new ArrayBuffer(44 + samples.length * bytesPerSample);
  const view = new DataView(buffer);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + samples.length * bytesPerSample, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * bytesPerSample, true);
  view.setUint16(32, bytesPerSample, true);
  view.setUint16(34, 8 * bytesPerSample, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, samples.length * bytesPerSample, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, samples[i] ?? 0));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += bytesPerSample;
  }
  return new Blob([view], { type: "audio/wav" });
}

function writeAscii(view: DataView, offset: number, value: string): void {
  for (let i = 0; i < value.length; i += 1) {
    view.setUint8(offset + i, value.charCodeAt(i));
  }
}
