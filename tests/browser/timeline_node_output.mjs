/** Direct canvas timeline output uses real browser MP4 rendering and versioned publication. */
import assert from "node:assert/strict";
import { launchBrowser } from "./engine.mjs";
const base = process.env.CREATION_WEB_URL ?? "http://127.0.0.1:5179";
const api = process.env.CREATION_API_URL ?? base;
const headers = { "X-OpenDirector-Request": "1" };
const health = await fetch(api + "/api/creation-test-health", { headers }).then((r) => r.json());
assert.equal(health.isolated, true);
const browser = await launchBrowser();
try {
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  page.setDefaultTimeout(20000);
  const errors = [];
  page.on("pageerror", (error) => { errors.push(error.message); console.error(error.message); });
  await page.addInitScript(() => {
    localStorage.setItem("opendirector.modelSetup.v1", "dismissed");
    localStorage.setItem("opendirector.locale.v1", "en-US");
  });
  const bootstrap = (url) => url.origin === new URL(base).origin && url.pathname === "/";
  await page.route(bootstrap, (route) => route.fulfill({ contentType: "text/html", body: "<html></html>" }));
  await page.goto(base + "/");
  const fixture = await page.evaluate(async () => {
    const { createDocument, createNode } = await import("/src/canvas/model.ts");
    const { addTimelineClip } = await import("/src/canvas/timeline.ts");
    const { importWorkspaceDocument } = await import("/src/backend/workspace.ts");
    const canvas = document.createElement("canvas");
    canvas.width = 160; canvas.height = 96;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#318175"; ctx.fillRect(0, 0, 160, 96);
    const node = createNode("timeline", { x: 150, y: 150 }, { title: "Direct timeline output" });
    node.data.timeline.width = 160; node.data.timeline.height = 96; node.data.timeline.fps = 24;
    node.data.timeline = addTimelineClip(node.data.timeline, {
      id: crypto.randomUUID(), kind: "image", title: "Green frame", src: canvas.toDataURL(), width: 160, height: 96,
    });
    node.data.timeline.tracks[0].clips[0].outPoint = 0.5;
    const saved = await importWorkspaceDocument({ ...createDocument("Timeline output " + crypto.randomUUID().slice(0, 8)), nodes: [node] });
    (await import("/src/backend/creationClient.ts")).creationClient(saved.id).close();
    await (await import("/src/backend/projects.ts")).releaseBackendProject();
    return { id: saved.id, item: node.id };
  });
  await page.unroute(bootstrap);
  const snapshot = () => fetch(`${api}/api/creation/projects/${fixture.id}`, { headers }).then((r) => r.json());
  await page.goto(`${base}/ai-tool/ai-canvas/${fixture.id}`);
  await page.getByText("Saved on this device", { exact: true }).waitFor();
  const select = async () => {
    await page.locator(".cw-workspace").focus();
    await page.keyboard.press("Shift+1");
    await page.locator(`.react-flow__node[data-id="${fixture.item}"]`).click();
  };
  await select();
  const panel = page.locator(".cw-selection-toolbar");
  await panel.waitFor();
  assert.equal(await page.locator(".cw-composer-dock").count(), 0);
  let releaseFirst;
  const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
  let firstPublish = true;
  await page.route("**/commands", async (route) => {
    if (route.request().postDataJSON().type === "timeline.publishRender" && firstPublish) {
      firstPublish = false;
      await firstGate;
    }
    return route.continue();
  });
  await panel.getByRole("button", { name: "Output video", exact: true }).dblclick();
  const pending = page.locator('.react-flow__node-video').filter({ has: page.locator('.cw-node-generating') });
  await pending.waitFor();
  const pendingId = await pending.getAttribute("data-id");
  assert.equal(await pending.locator('.cw-generating').count(), 1);
  await page.locator(`.react-flow__edge[data-id="timeline-pending:${pendingId}"]`).waitFor();
  assert.equal(await panel.locator('progress').count(), 0);
  await page.waitForTimeout(400);
  await page.screenshot({ path: "/tmp/opendirector-timeline-pending.png" });
  releaseFirst();
  await page.unroute("**/commands");
  await page.locator(`.react-flow__node[data-id="${pendingId}"] video`).waitFor();
  let saved = await snapshot();
  const videos = saved.canvasItems.filter((item) => item.kind === "video");
  await page.locator(`.react-flow__edge[data-id^="publication:${pendingId}:"]`).waitFor();
  assert.equal(videos[0].id, pendingId, "pending identity is published in place");
  assert.equal(videos.length, 1, "double-click submits one output");
  const asset = saved.assets.find((asset) => asset.id === videos[0].binding.objectRef.assetId);
  assert.equal(asset.provenance.source, "rendered");
  assert.equal(asset.metadata.width, 160);
  assert.equal(asset.metadata.height, 96);
  assert.ok(Math.abs(asset.metadata.durationSeconds - 0.5) < 0.1);
  assert.ok(videos[0].position.y > saved.canvasItems.find((item) => item.id === fixture.item).position.y);
  assert.equal(await page.locator(".cw-timeline-dialog").count(), 0);
  await page.getByRole("button", { name: "中文", exact: true }).click();
  await select();
  const chinese = page.locator(".cw-selection-toolbar");
  await chinese.waitFor();
  // Cancel before rendering completes; no output should be published.
  await chinese.getByRole("button", { name: "输出视频", exact: true }).click();
  await page.locator(".cw-timeline-pending").getByRole("button", { name: "取消合成", exact: true }).click();
  await chinese.getByRole("button", { name: "输出视频", exact: true }).waitFor();
  assert.equal((await snapshot()).canvasItems.filter((item) => item.kind === "video").length, 1);
  const publicationIds = [];
  let loseReply = true;
  await page.route("**/commands", async (route) => {
    const value = route.request().postDataJSON();
    if (value.type !== "timeline.publishRender") return route.continue();
    publicationIds.push(value.commandId);
    if (loseReply) {
      loseReply = false;
      return route.abort();
    }
    return route.continue();
  });
  await page.locator(`.react-flow__node[data-id="${fixture.item}"]`).focus();
  await page.keyboard.press("Meta+Enter");
  await page.locator(".cw-timeline-pending").getByRole("alert").waitFor().catch(async (error) => { console.error(await page.locator("body").innerText()); throw error; });
  const assetsBeforeRetry = (await snapshot()).assets.length;
  const retryId = await page.locator('.react-flow__node-video').filter({ has: page.locator('.cw-timeline-pending') }).getAttribute('data-id');
  await page.getByRole("button", { name: "重试输出", exact: true }).click();
  await page.locator(`.react-flow__node[data-id="${retryId}"] video`).waitFor();
  saved = await snapshot();
  assert.equal(saved.canvasItems.filter((item) => item.kind === "video").length, 1);
  assert.equal(saved.editorDocuments.length, 1);
  assert.equal(saved.assets.length, assetsBeforeRetry, "retry reuses the rendered asset");
  assert.ok(publicationIds.length >= 2);
  assert.equal(new Set(publicationIds).size, 1, "retry replays the same publication");
  assert.equal(await page.locator(".cw-timeline-dialog").count(), 0);
  await select();
  // The focused output control must support the Windows/Linux modifier too.
  await chinese.getByRole("button", { name: "输出视频", exact: true }).focus();
  const published = page.waitForResponse((response) => response.request().method() === "POST" && response.request().url().endsWith("/commands") && response.request().postDataJSON().type === "timeline.publishRender");
  await page.keyboard.press("Control+Enter");
  assert.equal((await published).status(), 200);
  assert.equal((await snapshot()).canvasItems.filter((item) => item.kind === "video").length, 1);
  await page.locator(`.react-flow__node[data-id="${pendingId}"] video`).waitFor();
  await page.locator(`.react-flow__node[data-id="${pendingId}"] .cw-node-version-button`).click();
  await page.getByRole("radio").nth(2).waitFor();
  assert.equal(await page.getByRole("radio").count(), 3);
  await page.locator(`[data-version-asset-id="${asset.id}"]`).click();
  const applied = page.waitForResponse((response) => response.request().method() === "POST" && response.request().url().endsWith("/commands") && response.request().postDataJSON().type === "canvas.applyVersion");
  await page.getByRole("button", { name: "应用到画布", exact: true }).click();
  assert.equal((await applied).status(), 200);
  const restored = (await snapshot()).canvasItems.find((item) => item.id === pendingId);
  assert.equal(restored.binding.objectRef.assetId, asset.id);
  assert.deepEqual(errors, []);
  await page.evaluate(async (id) => {
    (await import("/src/backend/creationClient.ts")).creationClient(id).close();
    await (await import("/src/backend/projects.ts")).releaseBackendProject();
  }, fixture.id);
  await page.reload();
  await page.getByText("Saved on this device", { exact: true }).waitFor();
  await page.locator(`.react-flow__edge[data-id^="publication:${pendingId}:"]`).waitFor();
  assert.equal((await snapshot()).canvasItems.filter((item) => item.kind === "video").length, 1);
  await page.screenshot({ path: process.env.TIMELINE_OUTPUT_SCREENSHOT ?? "/tmp/opendirector-timeline-output.png" });
  console.log(JSON.stringify({ isolated: true, realMp4: true, button: true, shortcut: true, bilingual: true, oneOutputThreeVersions: true, provenanceEdge: true, versionRestored: true, editorOpened: false }));
} finally {
  await browser.close();
}
