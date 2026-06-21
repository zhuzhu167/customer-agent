import { chromium } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";
import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { fileURLToPath } from "node:url";

const appUrl = process.env.WEB_VOICE_APP_URL ?? "http://127.0.0.1:5173";
const apiBaseUrl = process.env.VITE_SESSION_API_BASE_URL ?? "http://127.0.0.1:8000";
const timeoutMs = Number(process.env.WEB_VOICE_MANUAL_TIMEOUT_MS ?? 180000);
const browserChannel = process.env.WEB_VOICE_BROWSER_CHANNEL ?? "chrome";
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "../..");
const outputDir = path.resolve(
  repoRoot,
  process.env.WEB_VOICE_MANUAL_OUTPUT_DIR ?? "output/manual-mic-acceptance",
);

const createdSessions = [];

async function main() {
  await fs.mkdir(outputDir, { recursive: true });
  const rl = readline.createInterface({ input, output });
  const browser = await chromium.launch({
    headless: false,
    channel: browserChannel || undefined,
    args: ["--autoplay-policy=no-user-gesture-required"],
  });
  const context = await browser.newContext({
    permissions: ["microphone"],
  });
  await context.grantPermissions(["microphone"], { origin: appUrl });
  const page = await context.newPage();
  const consoleMessages = [];
  const pageErrors = [];
  page.on("console", (message) =>
    consoleMessages.push({
      type: message.type(),
      text: sanitizeConsoleText(message.text()),
    }),
  );
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

  let result;
  try {
    await installAcceptanceRecorder(page);
    await page.goto(appUrl, { waitUntil: "networkidle", timeout: timeoutMs });
    await page.getByText("浏览器模拟通话", { exact: false }).first().waitFor({
      state: "visible",
      timeout: timeoutMs,
    });
    await assertNoMockControls(page);

    await page.getByRole("button", { name: "debug" }).click();
    await page.getByRole("button", { name: "拨打电话" }).click();
    await waitForCreatedSession();

    output.write("\n请在打开的浏览器里完成真人麦克风验收：\n");
    output.write("1. 对麦克风说一句业务咨询，例如“我想了解办理业务需要哪些资料”。\n");
    output.write("2. 等待页面出现用户转写、客服准备回答、客服已回答，并确认能听到 Agent 语音。\n");
    output.write("3. 在 Agent 播报时主动说话一次，观察是否触发打断或恢复。\n");
    output.write("4. 点击“结束通话”，然后回到终端回答下面的问题。\n\n");

    await waitForOperator(rl, "完成浏览器内真人通话后按 Enter 继续验收记录...");
    const objective = await collectObjectiveSignals(page);
    const subjective = await collectSubjectiveChecklist(rl);
    await endActiveCall(page).catch(() => undefined);

    result = {
      ok: subjective.every((item) => item.pass) && objective.required_events_present,
      kind: "manual_microphone_acceptance",
      app_url: appUrl,
      api_base_url: apiBaseUrl,
      generated_at: new Date().toISOString(),
      sessions: createdSessions,
      objective,
      subjective,
      console: summarizeConsoleMessages(consoleMessages),
      page_errors: pageErrors,
      notes:
        "人工验收只记录页面事件、转写片段和主观勾选结果，不保存原始音频。若未通过，请保留本 JSON 作为调参依据。",
    };
  } finally {
    await browser.close();
    rl.close();
    await cleanupSessions();
  }

  const outputPath = path.join(outputDir, `manual-mic-acceptance-${timestampForFilename()}.json`);
  await fs.writeFile(outputPath, `${JSON.stringify(result, null, 2)}\n`, "utf-8");
  console.log(JSON.stringify({ ...result, output_path: outputPath }, null, 2));

  if (!result.ok) {
    throw new Error(`Manual microphone acceptance did not pass. Result saved to ${outputPath}`);
  }
}

async function installAcceptanceRecorder(page) {
  await page.addInitScript(() => {
    window.__webVoiceAcceptanceLog = [];
    const pushSnapshot = () => {
      const text = document.body?.innerText;
      if (!text) return;
      window.__webVoiceAcceptanceLog.push({
        at: new Date().toISOString(),
        text,
      });
      if (window.__webVoiceAcceptanceLog.length > 300) {
        window.__webVoiceAcceptanceLog.splice(0, window.__webVoiceAcceptanceLog.length - 300);
      }
    };

    window.addEventListener("DOMContentLoaded", () => {
      pushSnapshot();
      const observer = new MutationObserver(pushSnapshot);
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

async function collectObjectiveSignals(page) {
  const log = await page.evaluate(() => window.__webVoiceAcceptanceLog ?? []);
  const combinedText = log.map((item) => item.text).join("\n");
  const observedEvents = [
    "transcript.final",
    "agent.reply.preparing",
    "agent.reply.answered",
    "agent.reply.interrupted",
    "agent.reply.interrupt_recovered",
    "turn.silence_timeout",
    "session.error",
  ].filter((eventType) => combinedText.includes(eventType));
  return {
    observed_events: observedEvents,
    required_events_present:
      combinedText.includes("transcript.final") &&
      combinedText.includes("agent.reply.preparing") &&
      combinedText.includes("agent.reply.answered"),
    observed_transcript_samples: extractSamplesAfterLabel(log, "用户说话"),
    observed_reply_samples: extractSamplesAfterLabel(log, "客服已回答"),
    page_snapshot_count: log.length,
  };
}

async function collectSubjectiveChecklist(rl) {
  return [
    await askPassFail(rl, "麦克风授权和真实输入是否正常"),
    await askPassFail(rl, "用户说话内容是否在页面可读展示"),
    await askPassFail(rl, "是否听到 Agent 语音播报"),
    await askPassFail(rl, "Agent 回答是否像客服而不是静态样例"),
    await askPassFail(rl, "播报中主动说话时，打断或恢复行为是否可接受"),
    await askPassFail(rl, "结束通话后页面状态是否正确"),
  ];
}

async function askPassFail(rl, label) {
  const answer = await rl.question(`${label}？输入 y/n，可加备注：`);
  const normalized = answer.trim().toLowerCase();
  return {
    item: label,
    pass: normalized === "y" || normalized.startsWith("y "),
    raw_answer: answer.trim(),
  };
}

async function waitForOperator(rl, prompt) {
  await rl.question(prompt);
}

async function endActiveCall(page) {
  const endButton = page.getByRole("button", { name: "结束通话" });
  if (await endButton.isVisible().catch(() => false)) {
    await endButton.click();
    await page.getByText("通话已结束", { exact: false }).first().waitFor({ timeout: 15000 }).catch(() => undefined);
  }
}

async function cleanupSessions() {
  await Promise.all(
    createdSessions.map(async (sessionId) => {
      await fetch(`${apiBaseUrl}/api/v1/sessions/${sessionId}/end`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "manual_mic_acceptance_finished" }),
      }).catch(() => undefined);
    }),
  );
}

async function assertNoMockControls(page) {
  const bodyText = await page.locator("body").innerText();
  if (/\bmock\b/i.test(bodyText)) {
    throw new Error("Page still exposes mock text/control");
  }
}

function extractSamplesAfterLabel(log, label) {
  const samples = [];
  for (const snapshot of log) {
    const lines = snapshot.text.split("\n").map((line) => line.trim()).filter(Boolean);
    const index = lines.findIndex((line) => line.includes(label));
    if (index >= 0 && lines[index + 1]) {
      samples.push(lines[index + 1]);
    }
  }
  return [...new Set(samples)].slice(-5);
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

function timestampForFilename() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

await main();
