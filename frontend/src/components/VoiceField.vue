<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount } from "vue";
import * as THREE from "three";
import type { VoiceCallState } from "@/types/events";

const props = defineProps<{
  state: VoiceCallState;
  inputLevel: number;
  outputLevel: number;
  latencyLevel: "good" | "warning" | "bad";
}>();

const intensity = computed(() => {
  if (props.state === "speaking") return props.outputLevel;
  if (props.state === "listening") return props.inputLevel;
  if (props.state === "thinking") return 0.66;
  if (props.state === "connecting") return 0.48;
  if (props.state === "interrupted") return 0.3;
  if (props.state === "error" || props.state === "no_permission") return 0.16;
  return 0.22;
});

const fieldStyle = computed(() => ({
  "--voice-intensity": intensity.value.toFixed(2),
}));

// Three.js integration
const container = ref<HTMLElement | null>(null);
let scene: THREE.Scene;
let camera: THREE.PerspectiveCamera;
let renderer: THREE.WebGLRenderer;
let animationId: number;

let blob: THREE.Points;
let bgWaves: THREE.Points;
let rings: THREE.Group;
let horizontalWave: THREE.Points;

const createGlowTexture = () => {
  const canvas = document.createElement('canvas');
  canvas.width = 64;
  canvas.height = 64;
  const context = canvas.getContext('2d');
  if (context) {
    const gradient = context.createRadialGradient(32, 32, 0, 32, 32, 32);
    gradient.addColorStop(0, 'rgba(255,255,255,1)');
    gradient.addColorStop(0.2, 'rgba(255,255,255,0.8)');
    gradient.addColorStop(0.5, 'rgba(255,255,255,0.2)');
    gradient.addColorStop(1, 'rgba(255,255,255,0)');
    context.fillStyle = gradient;
    context.fillRect(0, 0, 64, 64);
  }
  return new THREE.CanvasTexture(canvas);
};

const initThree = () => {
  if (!container.value) return;

  scene = new THREE.Scene();
  // Use a wider FOV for the immersive background
  camera = new THREE.PerspectiveCamera(60, container.value.clientWidth / container.value.clientHeight, 0.1, 1000);
  camera.position.z = 8;

  renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
  renderer.setSize(container.value.clientWidth, container.value.clientHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  container.value.appendChild(renderer.domElement);

  const texture = createGlowTexture();

  // 1. Central Morphing Blob (IcosahedronGeometry for organic look)
  const blobGeometry = new THREE.IcosahedronGeometry(2.0, 32);
  const blobMaterial = new THREE.PointsMaterial({
    size: 0.09,
    vertexColors: true,
    transparent: true,
    opacity: 0.95,
    map: texture,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });

  const positionAttribute = blobGeometry.attributes.position;
  if (!positionAttribute) return;
  const blobPositions = positionAttribute.array as Float32Array;
  const blobColors = new Float32Array(blobPositions.length);
  const colorCyan = new THREE.Color("#38bdf8");
  const colorPurple = new THREE.Color("#a78bfa");

  for (let i = 0; i < blobPositions.length; i += 3) {
    const x = blobPositions[i];
    const y = blobPositions[i+1];

    if (x !== undefined && y !== undefined) {
      // Diagonal gradient mix
      const mixRatio = (x + y + 4.0) / 8.0;
      const c = colorPurple.clone().lerp(colorCyan, Math.max(0, Math.min(1, mixRatio)));
      blobColors[i] = c.r;
      blobColors[i+1] = c.g;
      blobColors[i+2] = c.b;
    }
  }
  blobGeometry.setAttribute('color', new THREE.BufferAttribute(blobColors, 3));
  blobGeometry.setAttribute('aOriginalPosition', new THREE.BufferAttribute(new Float32Array(blobPositions), 3));

  blob = new THREE.Points(blobGeometry, blobMaterial);
  scene.add(blob);

  // 2. Background Sweeping Waves
  const bgGeometry = new THREE.BufferGeometry();
  const bgCount = 12000;
  const bgPositions = new Float32Array(bgCount * 3);
  const bgColors = new Float32Array(bgCount * 3);

  for (let i = 0; i < bgCount; i++) {
    const x = (Math.random() - 0.5) * 45;
    const z = (Math.random() - 0.5) * 30 - 5;
    const y = (Math.random() - 0.5) * 25;

    bgPositions[i*3] = x;
    bgPositions[i*3+1] = y;
    bgPositions[i*3+2] = z;

    const mixRatio = (x + 22.5) / 45;
    const c = colorPurple.clone().lerp(colorCyan, Math.max(0, Math.min(1, mixRatio)));
    bgColors[i*3] = c.r;
    bgColors[i*3+1] = c.g;
    bgColors[i*3+2] = c.b;
  }
  bgGeometry.setAttribute('position', new THREE.BufferAttribute(bgPositions, 3));
  bgGeometry.setAttribute('color', new THREE.BufferAttribute(bgColors, 3));
  bgGeometry.setAttribute('aOriginalPosition', new THREE.BufferAttribute(new Float32Array(bgPositions), 3));

  const bgMaterial = new THREE.PointsMaterial({
    size: 0.05,
    vertexColors: true,
    transparent: true,
    opacity: 0.35,
    map: texture,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  bgWaves = new THREE.Points(bgGeometry, bgMaterial);
  scene.add(bgWaves);

  // 3. Glowing Concentric Rings
  rings = new THREE.Group();
  const createRing = (radius: number, color: number, opacity: number, thickness: number) => {
    const geo = new THREE.TorusGeometry(radius, thickness, 32, 128);
    const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false });
    return new THREE.Mesh(geo, mat);
  };
  rings.add(createRing(3.6, 0x38bdf8, 0.5, 0.02));
  rings.add(createRing(4.3, 0xa78bfa, 0.25, 0.015));
  rings.add(createRing(5.0, 0x38bdf8, 0.15, 0.04));
  scene.add(rings);

  // 4. Horizontal 3D Audio Wave
  const lineGeo = new THREE.BufferGeometry();
  const lineCount = 600;
  const linePos = new Float32Array(lineCount * 3);
  for(let i=0; i<lineCount; i++) {
    linePos[i*3] = (i / lineCount - 0.5) * 35; // x spreads widely
    linePos[i*3+1] = 0; // y
    linePos[i*3+2] = 0; // z
  }
  lineGeo.setAttribute('position', new THREE.BufferAttribute(linePos, 3));
  lineGeo.setAttribute('aOriginalPosition', new THREE.BufferAttribute(new Float32Array(linePos), 3));
  const lineMat = new THREE.PointsMaterial({
    size: 0.12,
    color: 0x38bdf8,
    transparent: true,
    opacity: 0.8,
    map: texture,
    depthWrite: false,
    blending: THREE.AdditiveBlending
  });
  horizontalWave = new THREE.Points(lineGeo, lineMat);
  scene.add(horizontalWave);

  window.addEventListener("resize", onWindowResize);
  animate();
};

let time = 0;
const animate = () => {
  animationId = requestAnimationFrame(animate);

  time += 0.012;
  const currentIntensity = intensity.value;
  const state = props.state;

  // Animate Blob
  if (blob) {
    const posAttr = blob.geometry.attributes.position;
    const origAttr = blob.geometry.attributes.aOriginalPosition;
    const normAttr = (blob.geometry as THREE.IcosahedronGeometry).attributes.normal;

    if (posAttr && origAttr && normAttr) {
      const positions = posAttr.array as Float32Array;
      const originals = origAttr.array as Float32Array;
      const normals = normAttr.array as Float32Array;

      blob.rotation.y = time * 0.15;
      blob.rotation.z = time * 0.05;

      if (state === "thinking") {
        blob.rotation.y += 0.03;
      }

      for (let i = 0; i < positions.length; i += 3) {
        const ox = originals[i];
        const oy = originals[i+1];
        const oz = originals[i+2];

        if (ox !== undefined && oy !== undefined && oz !== undefined) {
          const nx = normals[i] || 0;
          const ny = normals[i+1] || 0;
          const nz = normals[i+2] || 0;

          // 3D Noise approximation
          const noise = Math.sin(ox * 2 + time * 1.5) * Math.cos(oy * 2 + time) * Math.sin(oz * 2 + time * 1.2);

          let baseDisplacement = 0.15;
          if (state === "speaking") {
            baseDisplacement = 0.15 + currentIntensity * 0.5;
          } else if (state === "listening") {
            baseDisplacement = 0.1 + currentIntensity * 0.2;
          }

          const displacement = noise * baseDisplacement;

          positions[i] = ox + nx * displacement;
          positions[i+1] = oy + ny * displacement;
          positions[i+2] = oz + nz * displacement;
        }
      }
      posAttr.needsUpdate = true;
    }

    const mat = blob.material as THREE.PointsMaterial;
    if (state === "error" || state === "no_permission") {
      mat.color.setHex(0xef4444);
    } else if (state === "thinking") {
      mat.color.setHex(0xa78bfa);
    } else {
      mat.color.setHex(0xffffff);
    }
  }

  // Animate Background Waves
  if (bgWaves) {
    const posAttr = bgWaves.geometry.attributes.position;
    const origAttr = bgWaves.geometry.attributes.aOriginalPosition;

    if (posAttr && origAttr) {
      const positions = posAttr.array as Float32Array;
      const originals = origAttr.array as Float32Array;

      for (let i = 0; i < positions.length; i += 3) {
        const x = originals[i];
        const z = originals[i+2];
        if (x !== undefined && z !== undefined) {
          // Sweeping wave motion
          const yOffset = Math.sin(x * 0.15 + time) * 3 + Math.cos(z * 0.15 + time * 0.8) * 2;
          positions[i+1] = (originals[i+1] || 0) + yOffset;
        }
      }
      posAttr.needsUpdate = true;
    }
  }

  // Animate Rings
  if (rings) {
    const scaleTarget = 1 + currentIntensity * 0.1;
    rings.scale.lerp(new THREE.Vector3(scaleTarget, scaleTarget, scaleTarget), 0.1);
  }

  // Animate Horizontal Wave
  if (horizontalWave) {
    const posAttr = horizontalWave.geometry.attributes.position;
    const origAttr = horizontalWave.geometry.attributes.aOriginalPosition;

    if (posAttr && origAttr) {
      const positions = posAttr.array as Float32Array;
      const originals = origAttr.array as Float32Array;

      for (let i = 0; i < positions.length; i += 3) {
        const x = originals[i];
        if (x !== undefined) {
          const distFromCenter = Math.abs(x);
          // Audio visualizer like effect
          const attenuation = Math.max(0, 1 - distFromCenter / 12);
          const wave = Math.sin(x * 4 + time * 6) * Math.cos(x * 6 - time * 4);

          positions[i+1] = wave * attenuation * (0.4 + currentIntensity * 2.5);
        }
      }
      posAttr.needsUpdate = true;
    }
  }

  renderer.render(scene, camera);
};

const onWindowResize = () => {
  if (!container.value || !camera || !renderer) return;
  camera.aspect = container.value.clientWidth / container.value.clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(container.value.clientWidth, container.value.clientHeight);
};

onMounted(() => {
  initThree();
});

onBeforeUnmount(() => {
  if (animationId) cancelAnimationFrame(animationId);
  window.removeEventListener("resize", onWindowResize);
  if (renderer) renderer.dispose();
  if (container.value && renderer) {
    container.value.removeChild(renderer.domElement);
  }
});
</script>

<template>
  <div class="voice-field" :class="[`voice-field--${state}`, `voice-field--latency-${latencyLevel}`]" :style="fieldStyle" aria-hidden="true" ref="container">
    <div class="voice-field__vignette"></div>
    <div class="voice-field__spectrum">
      <span v-for="bar in 42" :key="bar" :style="{ '--bar-index': bar }"></span>
    </div>
  </div>
</template>

<style scoped>
.voice-field {
  position: fixed;
  inset: 0;
  z-index: 1;
  overflow: hidden;
}

.voice-field__vignette {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(circle at 50% 50%, transparent 40%, rgba(2, 4, 7, 0.8) 100%);
  z-index: 2;
}

/* Ensure Three.js canvas spans the container and sits behind UI elements */
:deep(canvas) {
  position: absolute;
  top: 0;
  left: 0;
  width: 100% !important;
  height: 100% !important;
  pointer-events: none;
  z-index: 1;
}

/* Keep the small bottom spectrum bar */
.voice-field__spectrum {
  position: absolute;
  right: 50%;
  bottom: 150px;
  display: flex;
  width: min(520px, 72vw);
  height: 54px;
  align-items: center;
  justify-content: center;
  gap: 6px;
  transform: translateX(50%);
  opacity: 0.54;
  z-index: 3;
}

.voice-field__spectrum span {
  width: 3px;
  /* Use a simpler height calc that is standard CSS */
  height: calc(8px + var(--bar-index) * 1px + var(--voice-intensity) * 34px);
  border-radius: 999px;
  background: linear-gradient(180deg, rgba(125, 211, 252, 0.82), rgba(167, 139, 250, 0.24));
  transform-origin: center;
  /* CSS calc doesn't support modulo operator %, fallback to a fixed animation time to fix linter */
  animation: spectrum-rise 1200ms ease-in-out infinite alternate;
}

@keyframes spectrum-rise {
  from {
    transform: scaleY(0.58);
  }
  to {
    transform: scaleY(1.12);
  }
}
</style>
