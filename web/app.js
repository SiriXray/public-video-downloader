"use strict";

const QUALITY_SELECTORS = {
  best: "bv*+ba/b",
  "1080p": "bv*[height<=1080]+ba/b[height<=1080]",
  "720p": "bv*[height<=720]+ba/b[height<=720]",
  "480p": "bv*[height<=480]+ba/b[height<=480]",
  audio: "ba/b",
};

const STATE_LABELS = {
  queued: "等待中",
  downloading: "下载中",
  processing: "合并处理中",
  ready: "文件已就绪",
  error: "下载失败",
  cancelled: "已取消",
};

const state = {
  video: null,
  tasks: [],
  links: null,
  polling: null,
  health: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const elements = {
  serviceGate: $("#serviceGate"),
  copyStartCommand: $("#copyStartCommand"),
  inspectForm: $("#inspectForm"),
  videoUrl: $("#videoUrl"),
  inspectButton: $("#inspectButton"),
  signalCanvas: $("#signalCanvas"),
  transferTrack: $("#transferTrack"),
  trackStatus: $("#trackStatus"),
  runtimeStatus: $("#runtimeStatus"),
  runtimeText: $("#runtimeText"),
  previewEmpty: $("#previewEmpty"),
  videoPreview: $("#videoPreview"),
  videoThumbnail: $("#videoThumbnail"),
  videoDuration: $("#videoDuration"),
  videoSource: $("#videoSource"),
  videoTitle: $("#videoTitle"),
  videoUploader: $("#videoUploader"),
  factExtractor: $("#factExtractor"),
  factFormats: $("#factFormats"),
  factFfmpeg: $("#factFfmpeg"),
  factWhisper: $("#factWhisper"),
  qualitySelect: $("#qualitySelect"),
  mergeFormat: $("#mergeFormat"),
  rightsConfirm: $("#rightsConfirm"),
  startDownload: $("#startDownload"),
  downloadNote: $("#downloadNote"),
  formatSelector: $("#formatSelector"),
  resolveLinks: $("#resolveLinks"),
  exportLinks: $("#exportLinks"),
  formatTableBody: $("#formatTableBody"),
  formatEmpty: $("#formatEmpty"),
  formatCount: $("#formatCount"),
  linkList: $("#linkList"),
  linkEmpty: $("#linkEmpty"),
  linkCount: $("#linkCount"),
  taskList: $("#taskList"),
  taskEmpty: $("#taskEmpty"),
  taskCount: $("#taskCount"),
  clearFinished: $("#clearFinished"),
  toastRegion: $("#toastRegion"),
};

const signalScene = (() => {
  const canvas = elements.signalCanvas;
  const context = canvas?.getContext("2d");
  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const animatedStages = new Set(["inspect", "transfer"]);
  const stageProgress = {
    idle: 0.08,
    source: 0.2,
    inspect: 0.42,
    ready: 0.62,
    transfer: 0.82,
    file: 1,
  };
  let stage = "idle";
  let animationFrame = 0;
  let lastFrameAt = 0;

  function hash(x, y, salt = 0) {
    const value = Math.sin(x * 12.9898 + y * 78.233 + salt * 37.719) * 43758.5453;
    return value - Math.floor(value);
  }

  function insidePolygon(x, y, points) {
    let inside = false;
    for (let index = 0, previous = points.length - 1; index < points.length; previous = index++) {
      const currentPoint = points[index];
      const previousPoint = points[previous];
      const intersects = currentPoint[1] > y !== previousPoint[1] > y
        && x < ((previousPoint[0] - currentPoint[0]) * (y - currentPoint[1]))
          / (previousPoint[1] - currentPoint[1]) + currentPoint[0];
      if (intersects) inside = !inside;
    }
    return inside;
  }

  function fitCanvas() {
    if (!canvas || !context) return null;
    const bounds = canvas.getBoundingClientRect();
    const width = Math.max(1, Math.round(bounds.width));
    const height = Math.max(1, Math.round(bounds.height));
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    const outputWidth = Math.round(width * pixelRatio);
    const outputHeight = Math.round(height * pixelRatio);
    if (canvas.width !== outputWidth || canvas.height !== outputHeight) {
      canvas.width = outputWidth;
      canvas.height = outputHeight;
    }
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    return { width, height };
  }

  function draw(time = 0) {
    const size = fitCanvas();
    if (!size || !context) return;
    const { width, height } = size;
    const progress = stageProgress[stage] ?? stageProgress.idle;
    const activeTime = motionQuery.matches ? 0 : time;
    const pulse = 0.5 + Math.sin(activeTime * 0.0025) * 0.5;
    const centerX = width * (width < 640 ? 0.5 : 0.47);
    const centerY = height * (width < 640 ? 0.43 : 0.46);
    const packetWidth = Math.min(width * 0.54, 440);
    const packetHeight = Math.min(height * 0.42, 250);
    const points = [
      [centerX - packetWidth * 0.5, centerY - packetHeight * 0.22],
      [centerX - packetWidth * 0.3, centerY - packetHeight * 0.5],
      [centerX + packetWidth * 0.42, centerY - packetHeight * 0.36],
      [centerX + packetWidth * 0.5, centerY + packetHeight * 0.25],
      [centerX + packetWidth * 0.24, centerY + packetHeight * 0.5],
      [centerX - packetWidth * 0.44, centerY + packetHeight * 0.32],
    ];

    context.clearRect(0, 0, width, height);
    context.lineWidth = 1;
    context.strokeStyle = "rgba(244, 244, 238, 0.18)";
    context.beginPath();
    points.forEach(([x, y], index) => index ? context.lineTo(x, y) : context.moveTo(x, y));
    context.closePath();
    context.stroke();

    const grid = width < 500 ? 7 : 8;
    const minX = Math.floor((centerX - packetWidth * 0.5) / grid) * grid;
    const maxX = centerX + packetWidth * 0.5;
    const minY = Math.floor((centerY - packetHeight * 0.5) / grid) * grid;
    const maxY = centerY + packetHeight * 0.5;
    const scanPosition = minX + ((activeTime * 0.11) % Math.max(packetWidth, 1));

    for (let y = minY; y <= maxY; y += grid) {
      for (let x = minX; x <= maxX; x += grid) {
        if (!insidePolygon(x, y, points)) continue;
        const distance = Math.hypot((x - centerX) / packetWidth, (y - centerY) / packetHeight);
        const noise = hash(x / grid, y / grid, Math.round(progress * 10));
        const density = 0.25 + progress * 0.43 + Math.max(0, 0.25 - distance) + pulse * 0.04;
        if (noise > density) continue;
        const highlighted = Math.abs(x - scanPosition) < grid * 1.4 && animatedStages.has(stage);
        context.fillStyle = highlighted
          ? "rgba(214, 255, 87, 0.96)"
          : noise < progress * 0.16
            ? "rgba(244, 244, 238, 0.94)"
            : `rgba(146, 146, 139, ${0.28 + noise * 0.5})`;
        const pixelSize = noise > 0.78 ? grid - 1 : Math.max(2, grid * 0.48);
        context.fillRect(x - pixelSize / 2, y - pixelSize / 2, pixelSize, pixelSize);
      }
    }

    context.strokeStyle = stage === "file" ? "rgba(214, 255, 87, 0.95)" : "rgba(244, 244, 238, 0.42)";
    context.beginPath();
    context.moveTo(points[1][0], points[1][1]);
    context.lineTo(points[2][0], points[2][1]);
    context.lineTo(points[3][0], points[3][1]);
    context.stroke();

    const railY = centerY + packetHeight * 0.67;
    const railStart = centerX - packetWidth * 0.56;
    const railWidth = packetWidth * 1.12;
    context.fillStyle = "rgba(244, 244, 238, 0.14)";
    context.fillRect(railStart, railY, railWidth, 1);
    context.fillStyle = "rgba(214, 255, 87, 0.92)";
    context.fillRect(railStart, railY - 1, railWidth * progress, 3);

    if (animatedStages.has(stage) && !motionQuery.matches) {
      const packetX = railStart + ((activeTime * 0.12) % railWidth);
      context.fillStyle = "#f4f4ee";
      context.fillRect(packetX, railY - 4, 9, 9);
    }

    context.fillStyle = "rgba(146, 146, 139, 0.72)";
    context.font = '500 9px "Cascadia Mono", Consolas, monospace';
    context.textAlign = "left";
    context.fillText(`PACKET / ${stage.toUpperCase()}`, railStart, railY + 20);
    context.textAlign = "right";
    context.fillText(`${String(Math.round(progress * 100)).padStart(3, "0")}%`, railStart + railWidth, railY + 20);
  }

  function animate(time) {
    if (time - lastFrameAt >= 32) {
      draw(time);
      lastFrameAt = time;
    }
    animationFrame = window.requestAnimationFrame(animate);
  }

  function refresh() {
    window.cancelAnimationFrame(animationFrame);
    animationFrame = 0;
    lastFrameAt = 0;
    if (animatedStages.has(stage) && !motionQuery.matches) {
      animationFrame = window.requestAnimationFrame(animate);
    } else {
      draw(performance.now());
    }
  }

  function setStage(nextStage) {
    stage = nextStage in stageProgress ? nextStage : "idle";
    if (canvas) canvas.dataset.stage = stage;
    refresh();
  }

  if (canvas && context) {
    if ("ResizeObserver" in window) {
      new ResizeObserver(refresh).observe(canvas);
    } else {
      window.addEventListener("resize", refresh);
    }
    motionQuery.addEventListener?.("change", refresh);
    setStage(stage);
  }

  return { setStage };
})();

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.appendChild(use);
  return svg;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new Error(payload?.error || `请求失败 (${response.status})`);
  }
  return payload;
}

function toast(message, type = "info") {
  const node = document.createElement("div");
  node.className = `toast${type === "error" ? " is-error" : ""}`;
  node.textContent = message;
  elements.toastRegion.appendChild(node);
  window.setTimeout(() => node.remove(), 3800);
}

async function copyText(value) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("浏览器未允许复制，请手动复制");
}

function setStage(stage, message) {
  elements.transferTrack.dataset.stage = stage;
  elements.trackStatus.textContent = message;
  signalScene.setStage(stage);
}

function switchTab(name) {
  $$("[data-tab]").forEach((button) => {
    const active = button.dataset.tab === name;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  $$("[data-panel]").forEach((panel) => {
    const active = panel.dataset.panel === name;
    panel.classList.toggle("is-active", active);
    panel.hidden = !active;
  });
}

function setInspectBusy(busy) {
  elements.inspectButton.disabled = busy;
  elements.inspectButton.classList.toggle("is-busy", busy);
  elements.inspectButton.setAttribute("aria-busy", String(busy));
  elements.inspectButton.querySelector("span").textContent = busy ? "正在解析" : "解析视频";
}

function updateActionState() {
  const allowed = Boolean(state.video && elements.rightsConfirm.checked);
  elements.startDownload.disabled = !allowed;
  elements.resolveLinks.disabled = !allowed;
  elements.downloadNote.textContent = state.video
    ? allowed
      ? "下载将在本机服务器执行，完成后由浏览器保存文件。"
      : "确认内容保存权限后可开始下载。"
    : "先解析一个公开视频链接。";
}

function renderVideo(video) {
  state.video = video;
  state.links = null;
  elements.previewEmpty.hidden = true;
  elements.videoPreview.hidden = false;
  elements.videoThumbnail.src = video.thumbnail || "";
  elements.videoThumbnail.alt = `${video.title} 封面`;
  elements.videoDuration.textContent = video.duration_text || "-";
  elements.videoSource.textContent = video.extractor || "-";
  elements.videoTitle.textContent = video.title || "未命名视频";
  elements.videoUploader.textContent = video.uploader || "-";
  elements.factExtractor.textContent = video.extractor || "-";
  elements.factFormats.textContent = String(video.format_count ?? 0);
  elements.formatCount.textContent = String(video.format_count ?? 0);
  elements.videoUrl.value = video.webpage_url || elements.videoUrl.value;
  elements.rightsConfirm.checked = false;
  elements.formatSelector.value = QUALITY_SELECTORS[elements.qualitySelect.value];
  renderFormats(video.formats || []);
  renderLinks(null);
  updateActionState();
}

function formatBytes(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "-";
  const units = ["B", "KiB", "MiB", "GiB"];
  let size = number;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function codecLabel(item) {
  const codecs = [item.vcodec, item.acodec].filter((value) => value && value !== "none");
  return codecs.length ? codecs.join(" / ") : "-";
}

function renderFormats(formats) {
  elements.formatTableBody.textContent = "";
  elements.formatEmpty.hidden = formats.length > 0;
  formats.forEach((item) => {
    const row = document.createElement("tr");
    const values = [
      item.id || "-",
      item.kind || "-",
      item.resolution || "-",
      item.ext || "-",
      codecLabel(item),
      formatBytes(item.filesize),
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index === 1) cell.className = "format-kind";
      row.appendChild(cell);
    });
    const actionCell = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "format-use";
    button.title = `使用格式 ${item.id}`;
    button.setAttribute("aria-label", `使用格式 ${item.id}`);
    button.appendChild(icon("check"));
    button.addEventListener("click", () => {
      elements.formatSelector.value = item.id || "";
      toast(`已选择格式 ${item.id}`);
    });
    actionCell.appendChild(button);
    row.appendChild(actionCell);
    elements.formatTableBody.appendChild(row);
  });
}

function renderLinks(payload) {
  elements.linkList.textContent = "";
  const items = payload?.items || [];
  elements.linkCount.textContent = String(items.length);
  elements.linkEmpty.hidden = items.length > 0;
  elements.exportLinks.disabled = items.length === 0;
  items.forEach((item) => {
    const article = document.createElement("article");
    article.className = "link-item";

    const meta = document.createElement("div");
    meta.className = "link-meta";
    const title = document.createElement("strong");
    title.textContent = `${item.media_type || "media"} · ${item.format_id || "-"}`;
    const detail = document.createElement("span");
    detail.textContent = [item.resolution, item.ext].filter(Boolean).join(" · ") || "-";
    meta.append(title, detail);

    const row = document.createElement("div");
    row.className = "link-row";
    const input = document.createElement("input");
    input.readOnly = true;
    input.value = item.url || "";
    input.setAttribute("aria-label", `${item.format_id || "媒体"} 直链`);
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "icon-button";
    copy.title = "复制直链";
    copy.setAttribute("aria-label", "复制直链");
    copy.appendChild(icon("copy"));
    copy.addEventListener("click", async () => {
      try {
        await copyText(input.value);
        toast("直链已复制");
      } catch (error) {
        toast(error.message, "error");
      }
    });
    row.append(input, copy);

    const headers = document.createElement("div");
    headers.className = "link-headers";
    const headerNames = Object.keys(item.http_headers || {});
    headers.textContent = headerNames.length ? `Headers: ${headerNames.join(", ")}` : "Headers: none";
    article.append(meta, row, headers);
    elements.linkList.appendChild(article);
  });
}

function taskDetail(task) {
  if (task.error) return task.error;
  if (task.state === "ready") return `${task.filename || "媒体文件"} · ${formatBytes(task.total_bytes)}`;
  if (task.state === "downloading") {
    const speed = task.speed ? `${formatBytes(task.speed)}/s` : "正在传输";
    const eta = Number.isFinite(task.eta) ? ` · 剩余 ${Math.max(0, Math.round(task.eta))} 秒` : "";
    return `${speed}${eta}`;
  }
  if (task.state === "processing") return "正在合并音视频流";
  return task.url || "";
}

function artifactLabel(kind) {
  return {
    frame: "首帧图片",
    audio: "MP3 音频",
    transcript: "文字转写",
  }[kind] || kind;
}

function artifactIcon(kind) {
  return { frame: "image", audio: "audio", transcript: "text" }[kind] || "file";
}

function renderArtifactTool(task, kind) {
  const artifact = task.artifacts?.[kind] || null;
  const tool = document.createElement("div");
  tool.className = `artifact-tool${artifact ? ` is-${artifact.state}` : ""}`;

  const heading = document.createElement("div");
  heading.className = "artifact-heading";
  const title = document.createElement("strong");
  title.append(icon(artifactIcon(kind)), document.createTextNode(artifactLabel(kind)));
  const status = document.createElement("span");
  status.textContent = artifact
    ? {
        queued: "排队中",
        processing: kind === "transcript" ? "本地识别中" : "处理中",
        ready: "已生成",
        error: "生成失败",
      }[artifact.state] || artifact.state
    : kind === "transcript"
      ? "可选本地模型"
      : "FFmpeg 本地处理";
  heading.append(title, status);
  tool.appendChild(heading);

  if (kind === "transcript" && (!artifact || artifact.state === "error")) {
    const options = document.createElement("div");
    options.className = "artifact-options";
    const model = document.createElement("select");
    model.setAttribute("aria-label", "转写模型");
    [
      ["tiny", "Tiny / 最快"],
      ["base", "Base / 推荐"],
      ["small", "Small / 更准"],
      ["medium", "Medium / 较慢"],
      ["large-v3", "Large v3 / 最慢"],
    ].forEach(([value, label]) => model.add(new Option(label, value, false, value === (artifact?.model || "base"))));
    const language = document.createElement("select");
    language.setAttribute("aria-label", "转写语言");
    [
      ["auto", "自动识别语言"],
      ["zh", "中文"],
      ["en", "英语"],
      ["ja", "日语"],
      ["ko", "韩语"],
    ].forEach(([value, label]) => language.add(new Option(label, value)));
    options.append(model, language);
    tool.appendChild(options);
    tool.dataset.modelSelect = "true";
    tool._modelSelect = model;
    tool._languageSelect = language;
  }

  const control = document.createElement("div");
  control.className = "artifact-control";
  if (artifact?.state === "ready") {
    const download = document.createElement("a");
    download.className = "artifact-action is-ready";
    download.href = artifact.download_url;
    download.append(icon("download"), document.createTextNode(`保存${artifactLabel(kind)}`));
    control.appendChild(download);
  } else {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "artifact-action";
    const busy = ["queued", "processing"].includes(artifact?.state);
    button.disabled = busy || (kind === "transcript" && state.health && !state.health.faster_whisper);
    button.append(
      icon(busy ? "refresh" : artifactIcon(kind)),
      document.createTextNode(
        busy ? status.textContent : artifact?.state === "error" ? "重试" : `生成${artifactLabel(kind)}`,
      ),
    );
    button.addEventListener("click", () => startArtifact(task.id, kind, tool));
    control.appendChild(button);
  }
  if (artifact?.error) {
    const error = document.createElement("p");
    error.className = "artifact-error";
    error.setAttribute("role", "alert");
    error.textContent = artifact.error;
    control.appendChild(error);
  } else if (kind === "transcript" && state.health && !state.health.faster_whisper) {
    const note = document.createElement("p");
    note.className = "artifact-note";
    note.textContent = "需先安装可选转写依赖，首帧和音频功能不受影响。";
    control.appendChild(note);
  }
  tool.appendChild(control);
  return tool;
}

function renderArtifactWorkspace(task) {
  const workspace = document.createElement("section");
  workspace.className = "artifact-workspace";
  const intro = document.createElement("div");
  intro.className = "artifact-intro";
  const label = document.createElement("p");
  label.textContent = "LOCAL MEDIA TOOLS";
  const heading = document.createElement("h4");
  heading.textContent = "下载完成后继续处理";
  const description = document.createElement("p");
  description.textContent = "在本机提取首帧、导出音频或生成带时间戳的文字稿。";
  intro.append(label, heading, description);
  const grid = document.createElement("div");
  grid.className = "artifact-grid";
  ["frame", "audio", "transcript"].forEach((kind) => grid.appendChild(renderArtifactTool(task, kind)));
  workspace.append(intro, grid);
  return workspace;
}

function renderTasks(tasks) {
  state.tasks = tasks;
  elements.taskList.textContent = "";
  elements.taskEmpty.hidden = tasks.length > 0;
  elements.taskCount.textContent = String(tasks.length);
  const hasActiveArtifact = (task) => Object.values(task.artifacts || {})
    .some((artifact) => ["queued", "processing"].includes(artifact.state));
  elements.clearFinished.disabled = !tasks.some((task) =>
    ["ready", "error", "cancelled"].includes(task.state) && !hasActiveArtifact(task)
  );

  tasks.forEach((task) => {
    const article = document.createElement("article");
    article.className = `task-item is-${task.state}`;

    const main = document.createElement("div");
    main.className = "task-main";
    const stateLabel = document.createElement("p");
    stateLabel.className = "task-state";
    stateLabel.textContent = STATE_LABELS[task.state] || task.state;
    const title = document.createElement("h3");
    title.className = "task-title";
    title.textContent = task.title || "等待解析";
    const subline = document.createElement("p");
    subline.className = "task-subline";
    subline.textContent = taskDetail(task);
    main.append(stateLabel, title, subline);

    const progress = document.createElement("div");
    progress.className = "task-progress";
    const progressLabel = document.createElement("div");
    progressLabel.className = "progress-label";
    const percent = document.createElement("span");
    percent.textContent = `${Math.round(task.progress || 0)}%`;
    const amount = document.createElement("span");
    amount.textContent = task.total_bytes ? `${formatBytes(task.downloaded_bytes)} / ${formatBytes(task.total_bytes)}` : "-";
    progressLabel.append(percent, amount);
    const progressBar = document.createElement("div");
    progressBar.className = "progress-bar";
    const fill = document.createElement("span");
    fill.style.width = `${Math.max(0, Math.min(100, task.progress || 0))}%`;
    progressBar.appendChild(fill);
    progress.append(progressLabel, progressBar);

    const actions = document.createElement("div");
    actions.className = "task-actions";
    if (task.state === "ready") {
      const download = document.createElement("a");
      download.className = "task-action primary";
      download.href = task.download_url;
      download.append(icon("download"), document.createTextNode("保存文件"));
      actions.appendChild(download);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "task-action icon-only";
    const downloadActive = ["queued", "downloading", "processing"].includes(task.state);
    const artifactActive = hasActiveArtifact(task);
    remove.disabled = artifactActive && !downloadActive;
    remove.title = artifactActive && !downloadActive ? "媒体工具处理中" : downloadActive ? "取消任务" : "移除任务";
    remove.setAttribute("aria-label", remove.title);
    remove.appendChild(icon(downloadActive ? "x" : "trash"));
    remove.addEventListener("click", () => removeTask(task.id));
    actions.appendChild(remove);

    article.append(main, progress, actions);
    if (task.state === "ready") article.appendChild(renderArtifactWorkspace(task));
    elements.taskList.appendChild(article);
  });

  const active = tasks.some((task) =>
    ["queued", "downloading", "processing"].includes(task.state)
    || Object.values(task.artifacts || {}).some((artifact) => ["queued", "processing"].includes(artifact.state))
  );
  if (active) {
    setStage("transfer", "任务正在本机下载与合并");
    startPolling();
  } else {
    stopPolling();
    if (tasks.some((task) => task.state === "ready")) {
      setStage("file", "文件已就绪，可保存到本地");
    } else if (state.video) {
      setStage("ready", `已识别 ${state.video.format_count || 0} 个可用格式`);
    } else {
      setStage("idle", "等待输入公开视频链接");
    }
  }
}

async function startArtifact(taskId, kind, tool) {
  const body = {};
  if (kind === "transcript") {
    body.model = tool._modelSelect?.value || "base";
    body.language = tool._languageSelect?.value || "auto";
  }
  try {
    const task = await api(`/api/downloads/${taskId}/artifacts/${kind}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    state.tasks = state.tasks.map((item) => item.id === task.id ? task : item);
    renderTasks(state.tasks);
    toast(`${artifactLabel(kind)}任务已开始`);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function inspectCurrentUrl(event) {
  event.preventDefault();
  const url = elements.videoUrl.value.trim();
  if (!url) return;
  setInspectBusy(true);
  setStage("inspect", "正在读取页面和可用媒体格式");
  try {
    const video = await api("/api/inspect", {
      method: "POST",
      body: JSON.stringify({ url }),
    });
    renderVideo(video);
    setStage("ready", `已识别 ${video.format_count || 0} 个可用格式`);
    toast("视频解析完成");
  } catch (error) {
    const hostname = (() => {
      try {
        return new URL(url).hostname.toLowerCase();
      } catch {
        return "";
      }
    })();
    const isX = hostname === "x.com" || hostname.endsWith(".x.com")
      || hostname === "twitter.com" || hostname.endsWith(".twitter.com");
    const message = isX && /超时|timeout|timed out/i.test(error.message)
      ? "X/Twitter 暂时无法连接，请检查本机网络或稍后重试"
      : error.message || "解析失败，请检查链接或稍后重试";
    setStage("source", message);
    toast(message, "error");
  } finally {
    setInspectBusy(false);
  }
}

async function startDownload() {
  if (!state.video || !elements.rightsConfirm.checked) return;
  elements.startDownload.disabled = true;
  setStage("transfer", "正在创建下载任务");
  try {
    const task = await api("/api/downloads", {
      method: "POST",
      body: JSON.stringify({
        url: state.video.webpage_url,
        quality: elements.qualitySelect.value,
        merge_format: elements.mergeFormat.value,
      }),
    });
    state.tasks = [task, ...state.tasks.filter((item) => item.id !== task.id)];
    renderTasks(state.tasks);
    switchTab("tasks");
    toast("已加入下载队列");
  } catch (error) {
    toast(error.message, "error");
    updateActionState();
  }
}

async function resolveLinks() {
  if (!state.video || !elements.rightsConfirm.checked) return;
  elements.resolveLinks.disabled = true;
  setStage("inspect", "正在解析所选格式的临时媒体地址");
  try {
    const payload = await api("/api/links", {
      method: "POST",
      body: JSON.stringify({
        url: state.video.webpage_url,
        format: elements.formatSelector.value.trim(),
      }),
    });
    state.links = payload;
    renderLinks(payload);
    setStage("ready", `已解析 ${payload.items.length} 条临时媒体直链`);
    toast("媒体直链解析完成");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    updateActionState();
  }
}

function exportLinks() {
  if (!state.links) return;
  const blob = new Blob([JSON.stringify(state.links, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `media-links-${state.video?.id || "video"}.json`;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function loadTasks() {
  try {
    const payload = await api("/api/downloads", { method: "GET", headers: {} });
    renderTasks(payload.tasks || []);
  } catch (error) {
    toast(error.message, "error");
  }
}

function startPolling() {
  if (state.polling) return;
  state.polling = window.setInterval(loadTasks, 900);
}

function stopPolling() {
  if (!state.polling) return;
  window.clearInterval(state.polling);
  state.polling = null;
}

async function removeTask(id) {
  try {
    await api(`/api/downloads/${id}`, { method: "DELETE", headers: {} });
    await loadTasks();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function clearFinished() {
  const finished = state.tasks.filter((task) =>
    ["ready", "error", "cancelled"].includes(task.state)
    && !Object.values(task.artifacts || {}).some((artifact) => ["queued", "processing"].includes(artifact.state))
  );
  for (const task of finished) {
    try {
      await api(`/api/downloads/${task.id}`, { method: "DELETE", headers: {} });
    } catch (error) {
      toast(error.message, "error");
      break;
    }
  }
  await loadTasks();
}

async function loadHealth() {
  try {
    const health = await api("/api/health", { method: "GET", headers: {} });
    state.health = health;
    elements.runtimeStatus.classList.toggle("is-ready", health.status === "ok");
    elements.runtimeStatus.classList.toggle("is-error", health.status !== "ok");
    elements.runtimeText.textContent = health.status === "ok" ? `yt-dlp ${health.yt_dlp}` : "服务依赖缺失";
    elements.factFfmpeg.textContent = health.ffmpeg ? "可用" : "未安装";
    elements.factWhisper.textContent = health.faster_whisper ? "可用" : "可选安装";
    renderTasks(state.tasks);
  } catch {
    elements.runtimeStatus.classList.add("is-error");
    elements.runtimeText.textContent = "服务未连接";
    elements.factFfmpeg.textContent = "未知";
    elements.factWhisper.textContent = "未知";
  }
}

function filePreviewCommand() {
  const filePath = decodeURIComponent(window.location.pathname)
    .replace(/^\/([A-Za-z]:)/, "$1")
    .replace(/\//g, "\\");
  const projectPath = filePath.replace(/\\web\\index\.html$/i, "");
  return `cd /d "${projectPath}" && start_web.cmd`;
}

function enableFilePreview() {
  elements.serviceGate.hidden = false;
  elements.runtimeStatus.classList.add("is-error");
  elements.runtimeText.textContent = "需要启动本地服务";
  elements.videoUrl.disabled = true;
  elements.inspectButton.disabled = true;
  setStage("idle", "请先通过 start_web.cmd 启动本地服务");
}

elements.inspectForm.addEventListener("submit", inspectCurrentUrl);
elements.startDownload.addEventListener("click", startDownload);
elements.resolveLinks.addEventListener("click", resolveLinks);
elements.exportLinks.addEventListener("click", exportLinks);
elements.clearFinished.addEventListener("click", clearFinished);
elements.rightsConfirm.addEventListener("change", updateActionState);
elements.qualitySelect.addEventListener("change", () => {
  elements.formatSelector.value = QUALITY_SELECTORS[elements.qualitySelect.value];
});
elements.videoThumbnail.addEventListener("error", () => {
  elements.videoThumbnail.removeAttribute("src");
  elements.videoThumbnail.alt = "封面暂不可用";
});
const tabButtons = $$("[data-tab]");
tabButtons.forEach((button, index) => {
  button.addEventListener("click", () => switchTab(button.dataset.tab));
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    let nextIndex = index;
    if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabButtons.length) % tabButtons.length;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % tabButtons.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = tabButtons.length - 1;
    const nextButton = tabButtons[nextIndex];
    switchTab(nextButton.dataset.tab);
    nextButton.focus();
  });
});

elements.copyStartCommand?.addEventListener("click", async () => {
  try {
    await copyText(filePreviewCommand());
    toast("启动命令已复制");
  } catch (error) {
    toast(error.message, "error");
  }
});

if (window.location.protocol === "file:") {
  enableFilePreview();
} else {
  loadHealth();
  loadTasks();
  updateActionState();
  const initialUrl = new URLSearchParams(window.location.search).get("url");
  if (initialUrl) {
    elements.videoUrl.value = initialUrl;
    elements.inspectForm.requestSubmit();
  }
}
