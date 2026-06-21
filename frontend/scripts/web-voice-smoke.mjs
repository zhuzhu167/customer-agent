import { chromium } from "@playwright/test";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const appUrl = process.env.WEB_VOICE_APP_URL ?? "http://127.0.0.1:5173";
const apiBaseUrl = process.env.VITE_SESSION_API_BASE_URL ?? "http://127.0.0.1:8000";
const smokeText = process.env.WEB_VOICE_SMOKE_TEXT ?? "这是一轮云服务语音接入冒烟测试。";
const smokeTextFragment = smokeText.replace(/[，。！？,.!?]/g, "");
const timeoutMs = Number(process.env.WEB_VOICE_SMOKE_TIMEOUT_MS ?? 90000);
const browserChannel = process.env.WEB_VOICE_BROWSER_CHANNEL ?? "chrome";
const execFileAsync = promisify(execFile);
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "../..");

const createdSessions = [];

async function main() {
  const audioPath = await buildFakeMicrophoneWav();
  const browser = await chromium.launch({
    headless: true,
    channel: browserChannel || undefined,
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${audioPath}`,
      "--autoplay-policy=no-user-gesture-required",
    ],
  });

  const context = await browser.newContext({
    permissions: ["microphone"],
  });
  await context.grantPermissions(["microphone"], { origin: appUrl });
  const page = await context.newPage();
  const consoleMessages = [];
  const pageErrors = [];
  page.on("console", (message) => consoleMessages.push({
    type: message.type(),
    text: sanitizeConsoleText(message.text()),
  }));
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("response", async (response) => {
    if (response.url().includes("/api/v1/sessions") && response.request().method() === "POST") {
      try {
        const body = await response.json();
        if (body?.session_id) createdSessions.push(body.session_id);
      } catch {
        // Ignore non-JSON responses.
      }
    }
  });

  try {
    await installTextRecorder(page);
    await page.goto(appUrl, { waitUntil: "networkidle", timeout: timeoutMs });
    await expectVisibleText(page, "浏览器模拟通话");
    await assertNoMockControls(page);

    await page.getByRole("button", { name: "debug" }).click();
    await expectVisibleText(page, "debug 模式");

    await page.getByRole("button", { name: "拨打电话" }).click();
    await waitForCreatedSession();
    await expectObservedText(page, "transcript.final");
    await expectObservedText(page, smokeTextFragment);
    await expectObservedText(page, "agent.reply.preparing");
    await expectObservedText(page, "agent.reply.answered");
    await page.getByRole("button", { name: "结束通话" }).click();
    await expectVisibleText(page, "通话已结束");

    const result = {
      ok: true,
      app_url: appUrl,
      api_base_url: apiBaseUrl,
      sessions: createdSessions,
      observed_events: ["transcript.final", "agent.reply.preparing", "agent.reply.answered"],
      observed_transcript_fragment: smokeTextFragment,
      console: summarizeConsoleMessages(consoleMessages),
      page_errors: pageErrors,
    };
    console.log(JSON.stringify(result, null, 2));
  } finally {
    await browser.close();
    await cleanupSessions();
    await fs.rm(audioPath, { force: true });
  }
}

async function buildFakeMicrophoneWav() {
  const filePath = path.join(os.tmpdir(), `web-voice-smoke-${Date.now()}.wav`);
  await execFileAsync(
    "python3",
    ["-m", "backend.app.worker.tts_fixture", "--output", filePath, "--text", smokeText],
    { cwd: repoRoot, timeout: timeoutMs },
  );
  return filePath;
}

async function cleanupSessions() {
  await Promise.all(
    createdSessions.map(async (sessionId) => {
      await fetch(`${apiBaseUrl}/api/v1/sessions/${sessionId}/end`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "browser_smoke_finished" }),
      }).catch(() => undefined);
    }),
  );
}

async function installTextRecorder(page) {
  await page.addInitScript(() => {
    window.__webVoiceSmokeTextLog = [];
    const pushText = () => {
      const text = document.body?.innerText;
      if (!text) return;
      window.__webVoiceSmokeTextLog.push(text);
      if (window.__webVoiceSmokeTextLog.length > 200) {
        window.__webVoiceSmokeTextLog.splice(0, window.__webVoiceSmokeTextLog.length - 200);
      }
    };

    window.addEventListener("DOMContentLoaded", () => {
      pushText();
      const observer = new MutationObserver(pushText);
      observer.observe(document.body, {
        childList: true,
        subtree: true,
        characterData: true,
      });
    });
  });
}

async function waitForCreatedSession() {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (createdSessions.length > 0) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Timed out waiting for backend session.create response");
}

async function expectVisibleText(page, text) {
  await page.getByText(text, { exact: false }).first().waitFor({ state: "visible", timeout: timeoutMs });
}

async function expectObservedText(page, text) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const observed = await page.evaluate(() => (window.__webVoiceSmokeTextLog ?? []).join("\n"));
    if (observed.includes(text)) {
      return;
    }
    await page.waitForTimeout(500);
  }
  throw new Error(`Timed out waiting for observed page text: ${text}`);
}

async function assertNoMockControls(page) {
  const bodyText = await page.locator("body").innerText();
  if (/\bmock\b/i.test(bodyText)) {
    throw new Error("Page still exposes mock text/control");
  }
}

function sanitizeConsoleText(text) {
  return text
    .replace(/access_token=([^&\s']+)/g, "access_token=<redacted>")
    .replace(/join_request=([^&\s']+)/g, "join_request=<redacted>")
    .replace(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, "<jwt-redacted>");
}

function summarizeConsoleMessages(messages) {
  const errors = messages.filter((item) => item.type === "error").map((item) => item.text);
  const livekitV0Fallback = errors.filter((text) =>
    text.includes("/rtc/v1") || text.includes("NegotiationError: negotiation timed out"),
  );
  const resource404 = errors.filter((text) => text.includes("Failed to load resource") && text.includes("404"));
  const otherErrors = errors.filter((text) => !livekitV0Fallback.includes(text) && !resource404.includes(text));

  return {
    error_count: errors.length,
    expected_livekit_v0_fallback_count: livekitV0Fallback.length,
    expected_resource_404_count: resource404.length,
    other_errors: otherErrors.slice(0, 6),
  };
}

await main();
