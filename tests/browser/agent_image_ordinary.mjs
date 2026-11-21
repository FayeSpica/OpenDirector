/** Measure ordinary image requests on empty and populated canvases. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { launchBrowser } from "./engine.mjs";

const locales = {
  "en-US": {
    prompt: "Could you make me a picture of a red mug on a pale blue table?",
    chat: "AI chat", openChat: "Chat with AI", message: "Message content", send: "Send message",
    saved: "Saved on this device", import: "Choose local asset files",
  },
  "zh-CN": {
    prompt: "帮我画一张图，浅蓝色桌上放着一个红色马克杯。",
    chat: "AI 对话", openChat: "与 AI 对话", message: "消息内容", send: "发送消息",
    saved: "已保存到本机", import: "选择本地素材文件",
  },
};
const alternatePrompts = {
  "en-US": "Draw a red coffee cup sitting on a light blue tabletop for me.",
  "zh-CN": "能画个红色咖啡杯放在浅蓝桌上吗？",
};
if (process.env.AGENT_IMAGE_PARAPHRASES === "1") {
  for (const [locale, prompt] of Object.entries(alternatePrompts)) locales[locale].prompt = prompt;
}
const cases = [
  { id: "en-empty", locale: "en-US", populated: false },
  { id: "en-populated", locale: "en-US", populated: true },
  { id: "zh-empty", locale: "zh-CN", populated: false },
  { id: "zh-populated", locale: "zh-CN", populated: true },
];
const fixedImagePrompt = "A red ceramic mug on a pale blue table in soft window light.";
const fixedCase = {
  id: "fixed", locale: "en-US", populated: false, imagePrompt: fixedImagePrompt,
  prompt: `Generate one image on the canvas. Set the generation size to 2048x2048 and output format to PNG. Use this exact image prompt: "${fixedImagePrompt}" Keep the image visible on the canvas.`,
};
const caseId = process.env.AGENT_IMAGE_CASE;
const selectedCases = caseId ? [...cases, fixedCase].filter((entry) => entry.id === caseId) : cases;
assert.ok(selectedCases.length, `Unknown AGENT_IMAGE_CASE: ${caseId}`);
const firstCanvasBudgetMs = 3_000;
const size = "2048x2048";
const web = loopback(process.env.CREATION_WEB_URL ?? "http://127.0.0.1:5179");
const api = loopback(process.env.CREATION_API_URL ?? "http://127.0.0.1:8019");
const runs = count("AGENT_IMAGE_RUNS", 1, 1);
const warmups = count("AGENT_IMAGE_WARMUPS", 0, 0);
const timeout = count("AGENT_IMAGE_TIMEOUT_MS", 120_000, 1);
const variant = process.env.AGENT_IMAGE_VARIANT ?? "baseline";
const baseHeaders = { "X-OpenDirector-Request": "1" };

function loopback(value) {
  const url = new URL(value);
  assert.equal(url.protocol, "http:");
  assert.equal(url.hostname, "127.0.0.1", "Use an isolated loopback service");
  return url.origin;
}

function count(name, fallback, minimum) {
  const value = Number(process.env[name] ?? fallback);
  assert.ok(Number.isSafeInteger(value) && value >= minimum, `Invalid ${name}`);
  return value;
}

async function request(path, { method = "GET", body, session, base = api } = {}) {
  const response = await fetch(base + path, {
    method,
    redirect: "error",
    headers: {
      ...baseHeaders,
      ...(session ? { "X-OpenDirector-Session": session } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${await response.text()}`);
  return response;
}

async function json(path, options) {
  return (await request(path, options)).json();
}

const healthPath = "/api/creation-test-health";
const [apiHealth, webHealth] = await Promise.all([json(healthPath), json(healthPath, { base: web })]);
assert.equal(apiHealth.isolated, true);
assert.equal(webHealth.isolated, true);
assert.ok(apiHealth.root);
assert.equal(apiHealth.root, webHealth.root, "The web and API must share a fixture root");
assert.equal(apiHealth.mode, "live", "Use the live benchmark server");

const models = await json("/api/model-configs");
function selectedModel(kind, name) {
  const options = models.filter((model) => model.kind === kind && (!name || model.modelId === name));
  assert.equal(options.length, 1, `Select one ${kind} model explicitly`);
  return options[0];
}
const agent = selectedModel("agent", process.env.AGENT_IMAGE_AGENT_MODEL);
const image = selectedModel("image", process.env.AGENT_IMAGE_MODEL);

async function createProject(project, projectId) {
  await json(project + "/commands", {
    method: "POST",
    body: {
      type: "project.create", projectId, commandId: crypto.randomUUID(),
      payload: { name: `Agent image latency ${projectId}` },
    },
  });
  const session = crypto.randomUUID();
  await request(project + "/open", { method: "POST", session });
  try {
    const { conversationId } = await json(project + "/assistant");
    await request(project + "/assistant/draft", {
      method: "PUT", session,
      body: { conversationId, runtime: "default", modelSelections: { agent: agent.id, image: image.id } },
    });
  } finally {
    await request(`/api/creation/sessions/${session}`, { method: "DELETE" });
  }
}

// Both endpoints are sampled on animation frames on the same page clock.
// The preview must be decoded and must intersect the visible canvas, not just be in the DOM.
function measureOnClick(button, { timeout, existingIds }) {
  const existing = new Set(existingIds);
  function visible(element, pane) {
    const box = element.getBoundingClientRect();
    const clip = pane.getBoundingClientRect();
    const left = Math.max(0, clip.left, box.left);
    const right = Math.min(window.innerWidth, clip.right, box.right);
    const top = Math.max(0, clip.top, box.top);
    const bottom = Math.min(window.innerHeight, clip.bottom, box.bottom);
    if (left >= right || top >= bottom || getComputedStyle(element).visibility !== "visible") return false;
    return [.5, .25, .75].some((x) => [.5, .25, .75].some((y) =>
      element.contains(document.elementFromPoint(left + (right - left) * x, top + (bottom - top) * y))));
  }

  button.addEventListener("click", () => {
    const started = performance.now();
    const decoded = new WeakMap();
    const pending = new WeakMap();
    const sample = { clickEpochMs: performance.timeOrigin + started, firstCanvasMs: null, imageCompleteMs: null };
    window.agentImageLatency = sample;
    let resolveSample;
    window.agentImageLatencyDone = new Promise((resolve) => { resolveSample = resolve; });
    let frameId = requestAnimationFrame(frame);
    const deadline = setTimeout(() => {
      cancelAnimationFrame(frameId);
      resolveSample(null);
    }, timeout);

    function frame() {
      const pane = document.querySelector(".react-flow");
      const node = pane && [...pane.querySelectorAll(".react-flow__node-image")].find((element) => {
        const id = element.dataset.id;
        return id && !existing.has(id) && (sample.firstItemId === undefined || sample.firstItemId === id)
          && visible(element, pane);
      });
      if (node) {
        const elapsed = performance.now() - started;
        if (sample.firstCanvasMs === null) {
          sample.firstCanvasMs = elapsed;
          sample.firstItemId = node.dataset.id;
        }
        const preview = node.querySelector("img.cw-image-preview");
        if (preview?.naturalWidth > 0 && visible(preview, pane)) {
          const source = preview.currentSrc;
          if (decoded.get(preview) === source) {
            sample.imageCompleteMs = elapsed;
            sample.resultItemId = node.dataset.id;
            sample.width = preview.naturalWidth;
            sample.height = preview.naturalHeight;
            clearTimeout(deadline);
            resolveSample(sample);
            return;
          }
          if (pending.get(preview) !== source) {
            pending.set(preview, source);
            preview.decode().then(() => decoded.set(preview, source)).catch(() => {});
          }
        }
      }
      frameId = requestAnimationFrame(frame);
    }
  }, { capture: true, once: true });
}

async function verify(project, prompt, before, timing, previewDigest, exactImagePrompt) {
  const deadline = performance.now() + timeout;
  let conversation;
  do {
    conversation = await json(project + "/assistant");
    if (!conversation.activeTurnId && conversation.messages.length === 2) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  } while (performance.now() < deadline);

  assert.equal(conversation.activeTurnId, undefined, "Agent must finish the turn");
  assert.equal(conversation.messages.length, 2);
  const [user, reply] = conversation.messages;
  assert.equal(user.text, prompt);
  assert.equal(user.runtime, "default");
  assert.equal(user.model, agent.modelId);
  assert.equal(user.modelSelections.image, image.id);
  assert.equal(reply.status, "complete", `Agent turn ended in ${reply.status}`);

  const [snapshot, lineage] = await Promise.all([
    json(project),
    json(`${project}/assistant/turns/${user.id}/jobs`),
  ]);
  assert.equal(snapshot.jobs.length, 1, "The project must contain exactly one job");
  assert.equal(lineage.length, 1, "The turn must submit exactly one job");
  const [job] = snapshot.jobs;
  assert.equal(job.id, lineage[0].jobId);
  assert.equal(job.agentRunId, user.id);
  assert.equal(job.originItemId, timing.firstItemId);
  assert.equal(job.status, "succeeded");
  assert.equal(lineage[0].status, "succeeded");
  assert.equal(job.adopted, true);
  assert.equal(lineage[0].adopted, true);
  assert.equal(job.technicalCheck?.status, "passed");
  assert.ok(job.createdAt);
  assert.equal(job.inputSnapshot.spec.kind, "image");
  assert.equal(job.inputSnapshot.spec.modelConfigId, image.id);
  const generationPrompt = job.inputSnapshot.spec.prompt;
  if (exactImagePrompt) assert.equal(generationPrompt, exactImagePrompt);
  assert.match(generationPrompt, /mug|cup|[杯盃]/iu, "Job must preserve the requested subject");
  assert.match(generationPrompt, /red|红|紅/iu, "Job must preserve the requested mug color");
  assert.match(generationPrompt, /blue|蓝|藍/iu, "Job must preserve the requested table color");
  assert.deepEqual(job.inputSnapshot.references, []);
  assert.equal(job.execution.size, size);
  assert.equal(job.execution.outputFormat, "png");
  assert.equal(`${timing.width}x${timing.height}`, size);
  const asset = snapshot.assets.find((entry) => entry.id === job.asset.assetId);
  assert.equal(asset?.kind, "image");
  for (const old of before.canvasItems) {
    const current = snapshot.canvasItems.find((item) => item.id === old.id);
    assert.ok(current, "The existing image node must survive the request");
    for (const field of ["kind", "visible", "binding", "position"]) assert.deepEqual(current[field], old[field]);
  }
  for (const old of before.assets) {
    const current = snapshot.assets.find((entry) => entry.id === old.id);
    assert.equal(current?.fingerprint, old.fingerprint, "The existing image bytes must be preserved");
  }
  const oldIds = new Set(before.canvasItems.map((item) => item.id));
  const nodes = snapshot.canvasItems.filter((item) => !oldIds.has(item.id));
  assert.equal(nodes.length, 1, "The request must create exactly one new node");
  const [node] = nodes;
  assert.equal(node.kind, "image");
  assert.equal(node.visible, true);
  assert.equal(node.id, timing.firstItemId);
  assert.equal(node.id, timing.resultItemId);
  const spec = snapshot.generationSpecs.find((entry) => entry.id === node.binding.objectRef.id);
  assert.equal(spec?.id, job.inputSnapshot.spec.id, "The measured node must use the job's generation spec");
  const result = snapshot.results.find((entry) => entry.id === spec?.currentResultId);
  const displayed = node.binding.materialOverride ?? result?.asset ?? node.binding.objectRef;
  assert.equal(displayed.assetId, asset.id, "Saved canvas must display the job output");
  const response = await request(`${project}/assets/${asset.id}/content`);
  assert.match(response.headers.get("content-type"), /^image\//);
  const bytes = Buffer.from(await response.arrayBuffer());
  assert.ok(bytes.length > 0);
  assert.equal(asset.fingerprint, `sha256:${createHash("sha256").update(bytes).digest("hex")}`);
  assert.equal(previewDigest, asset.fingerprint, "The rendered preview must contain the job output bytes");
  return {
    turnId: user.id, jobId: job.id, assetId: asset.id, jobCreatedAt: job.createdAt,
    execution: job.execution, adapterParameters: job.adapterParameters, generationPrompt,
  };
}

async function prepareCanvas(project, page, scenario, labels) {
  if (scenario.populated) {
    await page.getByLabel(labels.import, { exact: true }).setInputFiles({
      name: "existing-image.png", mimeType: "image/png",
      buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAGUlEQVR4nGNsYGhgIAUwkaR6VMOohiGlAQBCPQEgiSD+iQAAAABJRU5ErkJggg==", "base64"),
    });
    await page.locator(".react-flow__node-image").waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll(".react-flow__node-image img.cw-image-preview")]
      .some((preview) => preview.complete && preview.naturalWidth > 0));
  }
  let before = await json(project);
  if (scenario.populated) {
    const deadline = performance.now() + timeout;
    while ((before.canvasItems.length !== 1 || before.assets.length !== 1) && performance.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      before = await json(project);
    }
  }
  assert.equal(before.jobs.length, 0, "Fixture must not have a generation job");
  assert.equal(before.canvasItems.length, scenario.populated ? 1 : 0);
  if (scenario.populated) {
    const [old] = before.canvasItems;
    assert.equal(old.kind, "image");
    assert.equal(old.visible, true);
    assert.equal(before.assets.length, 1);
    assert.equal(before.assets[0].kind, "image");
    await page.locator(`.react-flow__node-image[data-id="${old.id}"]`).waitFor();
  }
  return before;
}

async function run(browser, scenario, sample, warmup) {
  const projectId = crypto.randomUUID();
  const project = `/api/creation/projects/${projectId}`;
  const errors = [];
  const { locale } = scenario;
  const labels = locales[locale];
  const page = await browser.newPage({ locale, viewport: { width: 1500, height: 1000 } });
  try {
    await createProject(project, projectId);
    page.setDefaultTimeout(timeout);
    page.on("pageerror", (error) => errors.push(error.message));
    await page.addInitScript((locale) => {
      localStorage.setItem("opendirector.modelSetup.v1", "dismissed");
      localStorage.setItem("opendirector.locale.v1", locale);
    }, locale);
    await page.goto(`${web}/ai-tool/ai-canvas/${projectId}?creation=unified`);
    await page.getByText(labels.saved, { exact: true }).waitFor({ state: "attached" });
    assert.equal(await page.locator(".react-flow__node").count(), 0);
    const before = await prepareCanvas(project, page, scenario, labels);
    const existingIds = before.canvasItems.map((item) => item.id);
    const panel = page.getByRole("complementary", { name: labels.chat, exact: true });
    if (!(await panel.isVisible())) {
      await page.getByRole("button", { name: labels.openChat, exact: true }).click();
    }
    const prompt = scenario.prompt ?? labels.prompt;
    await panel.getByRole("textbox", { name: labels.message, exact: true }).fill(prompt);
    const send = panel.getByRole("button", { name: labels.send, exact: true });
    await send.evaluate(measureOnClick, { timeout, existingIds });
    await send.click();
    const timing = await page.evaluate(() => window.agentImageLatencyDone);
    assert.ok(timing, "Image did not become visible before the sample deadline");
    assert.ok(timing.firstCanvasMs > 0 && timing.imageCompleteMs >= timing.firstCanvasMs);
    const previewDigest = await page.locator(`.react-flow__node[data-id="${timing.resultItemId}"] img.cw-image-preview`).evaluate(async (preview) => {
      const bytes = await (await fetch(preview.currentSrc)).arrayBuffer();
      const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
      return `sha256:${[...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("")}`;
    });
    const ids = await verify(project, prompt, before, timing, previewDigest, scenario.imagePrompt);
    assert.deepEqual(errors, []);
    return { sample, warmup, projectId, existingIds, ...ids, ...timing,
      withinBudget: timing.firstCanvasMs <= firstCanvasBudgetMs };
  } catch (error) {
    const timing = await page.evaluate(() => window.agentImageLatency).catch(() => null);
    const conversation = await json(project + "/assistant").catch(() => null);
    const snapshot = await json(project).catch(() => null);
    return { sample, warmup, projectId, timing, error: String(error), pageErrors: errors,
      turnId: conversation?.messages.find((message) => message.role === "user")?.id,
      turnStatus: conversation?.messages.at(-1)?.status,
      jobs: snapshot?.jobs.map(({ id, status, phase }) => ({ id, status, phase })),
    };
  } finally {
    await page.close();
  }
}

const browser = await launchBrowser();
console.log(JSON.stringify({ type: "configuration", variant, runtime: "default", mode: apiHealth.mode ?? "fixture",
  cases: selectedCases.map((entry) => ({ ...entry, prompt: entry.prompt ?? locales[entry.locale].prompt })),
  imageModel: image.modelId, agentModel: agent.modelId, runs, warmups, timeout, firstCanvasBudgetMs,
  browser: browser.browserType().name(), browserVersion: browser.version(), platform: process.platform, arch: process.arch,
}));
let failed = false;
try {
  for (const scenario of selectedCases) {
    const measured = [];
    let failures = 0;
    for (let sample = 1; sample <= warmups + runs; sample++) {
      const result = await run(browser, scenario, sample, sample <= warmups);
      console.log(JSON.stringify({ type: "sample", variant, case: scenario.id, ...result }));
      if (result.error) failures++;
      else if (!result.warmup) measured.push(result);
    }
    const stats = (metric) => {
      const sorted = measured.map((result) => result[metric]).sort((a, b) => a - b);
      return { p50: sorted[Math.ceil(sorted.length * .5) - 1] ?? null,
        p90: sorted[Math.ceil(sorted.length * .9) - 1] ?? null, min: sorted.at(0) ?? null, max: sorted.at(-1) ?? null };
    };
    console.log(JSON.stringify({ type: "summary", variant, case: scenario.id, validRuns: measured.length, failures,
      withinBudget: measured.filter((result) => result.withinBudget).length,
      firstCanvasMs: stats("firstCanvasMs"), imageCompleteMs: stats("imageCompleteMs") }));
    failed ||= failures > 0;
  }
} finally {
  await browser.close();
}
if (failed) process.exitCode = 1;
