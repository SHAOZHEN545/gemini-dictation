import { LIVE_RECORDING_SECONDS, SAMPLE_RATE, clockText, decodeToPcm, transcribeLive } from "./live.js";
import { LocalRecorder, listMicrophones, primeAudio } from "./recorder.js";
import { deleteJob, loadJobs, saveJob } from "./store.js";
import { DriveVocabulary } from "./drive-vocabulary.js";

// Same scheduling rules as the desktop app (see MAX_PARALLEL_JOBS in gui.py).
const MAX_PARALLEL_JOBS = 3;
const MAX_ATTEMPTS = 4;
const RETRY_BACKOFF_SECONDS = [10, 30, 60];
const QUOTA_ERROR = /\b(429|409)\b|resource.?exhausted|quota|rate.?limit/i;
const TRANSIENT_ERROR = /\b(1006|1011|1013|500|502|503|504)\b|unavailable|overloaded|deadline|timed? ?out|connection|连接|没有返回/i;
const FATAL_ERROR = /api.?key|permission|\b(400|401|403)\b|安全时长/i;
const KEY_STORAGE = "gemini-api-key";
const VOCABULARY_STORAGE = "personal-vocabulary";

const $ = (id) => document.getElementById(id);
const ui = {
  settings: $("settings"),
  settingsSummary: $("settings-summary"),
  keyForm: $("key-form"),
  keyInput: $("key-input"),
  keyStatus: $("key-status"),
  forgetKey: $("forget-key"),
  mode: $("mode"),
  microphone: $("microphone"),
  vocabularyStatus: $("vocabulary-status"),
  showVocabulary: $("show-vocabulary"),
  importFile: $("import-file"),
  record: $("record"),
  next: $("next"),
  discard: $("discard"),
  status: $("status"),
  timer: $("timer"),
  meter: $("meter"),
  levelStatus: $("level-status"),
  queueSummary: $("queue-summary"),
  clearDone: $("clear-done"),
  copyAll: $("copy-all"),
  empty: $("empty"),
  jobs: $("jobs"),
  vocabularyDialog: $("vocabulary-dialog"),
  vocabularyFilter: $("vocabulary-filter"),
  vocabularyList: $("vocabulary-list"),
  vocabularyEditor: $("vocabulary-editor"),
  saveVocabulary: $("save-vocabulary"),
  driveClientId: $("drive-client-id"),
  driveApiKey: $("drive-api-key"),
  driveProjectNumber: $("drive-project-number"),
  drivePick: $("drive-pick"),
  driveRefresh: $("drive-refresh"),
  driveDisconnect: $("drive-disconnect"),
  driveStatus: $("drive-status"),
  toast: $("toast"),
};

let jobs = []; // Recording order; the list shows the newest first.
let nextJobNumber = 1;
let cooldownUntil = 0; // After a quota refusal, queued jobs wait instead of piling on.
let vocabulary = [];
const drive = new DriveVocabulary();
let keyVerified = false;
let recorder = null;
let session = null; // Mode and vocabulary captured when a recording starts.
let recordingStarted = 0;
let levels = new Array(66).fill(0);
let wakeLock = null;

// ---------- helpers ----------

function apiKey() {
  return (localStorage.getItem(KEY_STORAGE) || "").trim();
}

function setStatus(text) {
  ui.status.textContent = text;
}

let toastTimer;
function toast(text) {
  ui.toast.textContent = text;
  ui.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (ui.toast.hidden = true), 2200);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const field = document.createElement("textarea");
    field.value = text;
    document.body.append(field);
    field.select();
    document.execCommand("copy");
    field.remove();
  }
}

function timeOfDay(ms) {
  return new Date(ms).toLocaleTimeString("zh-CN", { hour12: false });
}

/** Seconds to wait before retrying a failed job, or null when retrying cannot help. */
function retryDelay(message, attempts) {
  if (attempts >= MAX_ATTEMPTS || FATAL_ERROR.test(message)) return null;
  if (!(QUOTA_ERROR.test(message) || TRANSIENT_ERROR.test(message))) return null;
  const suggested = message.match(/retry (?:in|after) (\d+(?:\.\d+)?)\s*s|retryDelay\W+(\d+)s/i);
  if (suggested) return Math.ceil(Number(suggested[1] || suggested[2])) + 1;
  return RETRY_BACKOFF_SECONDS[Math.min(attempts, RETRY_BACKOFF_SECONDS.length) - 1];
}

function recordingLimit() {
  return LIVE_RECORDING_SECONDS;
}

// ---------- vocabulary ----------

function parseVocabulary(text) {
  const terms = text.split(/\r?\n/).map((line) => line.trim()).filter((line) => line && !line.startsWith("#"));
  const unique = [...new Set(terms)];
  if (unique.length > 1000) throw new Error("Gemini 最多接受 1,000 个词条。");
  return unique;
}

function loadVocabulary() {
  const saved = localStorage.getItem(VOCABULARY_STORAGE);
  try {
    if (saved !== null) {
      vocabulary = parseVocabulary(saved);
    } else {
      // Keep terms already cached by earlier versions of the web app.
      const legacy = JSON.parse(localStorage.getItem("vocabulary-cache") || "[]");
      vocabulary = parseVocabulary(Array.isArray(legacy) ? legacy.join("\n") : "");
      if (vocabulary.length) localStorage.setItem(VOCABULARY_STORAGE, vocabulary.join("\n"));
    }
    ui.vocabularyStatus.textContent = `个人词库：${vocabulary.length} 项（仅本设备）`;
  } catch (error) {
    ui.vocabularyStatus.textContent = `个人词库需要检查：${error.message}`;
  }
  refreshSettingsSummary();
}

function driveConfig() {
  const clientId = ui.driveClientId.value.trim();
  const apiKey = ui.driveApiKey.value.trim();
  const projectNumber = ui.driveProjectNumber.value.trim();
  if (!clientId || !apiKey || !/^\d+$/.test(projectNumber)) {
    throw new Error("请填写网页 OAuth 客户端 ID、Picker API key 和数字项目编号");
  }
  localStorage.setItem("drive-client-id", clientId);
  localStorage.setItem("drive-api-key", apiKey);
  localStorage.setItem("drive-project-number", projectNumber);
  return { clientId, apiKey, projectNumber };
}

function showDriveState() {
  if (drive.fileId) {
    const ready = drive.loadedText !== null;
    ui.driveStatus.textContent = `${drive.fileName} · ${ready ? "已读取" : "需要重新读取"}`;
    ui.vocabularyStatus.textContent = ready
      ? `个人词库：${vocabulary.length} 项（Google Drive：${drive.fileName}）`
      : `个人词库：Drive 文件 ${drive.fileName} 尚未读取`;
    ui.saveVocabulary.textContent = "保存到 Google Drive";
  } else {
    ui.driveStatus.textContent = "尚未连接 Drive 文件";
    ui.vocabularyStatus.textContent = `个人词库：${vocabulary.length} 项（仅本设备）`;
    ui.saveVocabulary.textContent = "保存到本设备";
  }
  refreshSettingsSummary();
}

async function readDriveVocabulary() {
  const { clientId } = driveConfig();
  const text = await drive.read(clientId);
  vocabulary = parseVocabulary(text);
  ui.vocabularyEditor.value = text;
  renderVocabulary();
  showDriveState();
}

async function freshVocabulary() {
  if (!drive.fileId) return true;
  try {
    await readDriveVocabulary();
    return true;
  } catch (error) {
    drive.loadedText = null;
    showDriveState();
    setStatus(`无法读取 Drive 词库：${error.message}`);
    return false;
  }
}

function vocabularySortKey(term) {
  return [/^[\x00-\x7f]/.test(term) ? 0 : 1, term.toLocaleLowerCase()];
}

function renderVocabulary() {
  const filter = ui.vocabularyFilter.value.trim().toLocaleLowerCase();
  const sorted = [...vocabulary].sort((a, b) => {
    const [ga, ka] = vocabularySortKey(a);
    const [gb, kb] = vocabularySortKey(b);
    return ga - gb || ka.localeCompare(kb, "zh-CN");
  });
  ui.vocabularyList.replaceChildren(
    ...sorted
      .filter((term) => term.toLocaleLowerCase().includes(filter))
      .map((term) => Object.assign(document.createElement("li"), { textContent: term })),
  );
}

// ---------- settings ----------

function refreshSettingsSummary() {
  const key = apiKey() ? (keyVerified ? "Key 已验证" : "Key 已保存") : "未设置 Key";
  ui.settingsSummary.textContent = `${key} · ${ui.mode.value} · 个人词库 ${vocabulary.length} 项`;
}

function refreshKeyStatus() {
  if (apiKey()) {
    ui.keyStatus.textContent = `API key：${keyVerified ? "本次转写已成功验证" : "已保存在这个浏览器；尚未向 Google 验证"}`;
    ui.forgetKey.hidden = false;
  } else {
    ui.keyStatus.textContent = "API key：尚未设置（可从 Bitwarden 复制粘贴）";
    ui.forgetKey.hidden = true;
  }
  refreshSettingsSummary();
}

async function refreshMicrophones() {
  try {
    const microphones = await listMicrophones();
    const saved = localStorage.getItem("microphone") || "";
    const options = [new Option("系统默认", "")];
    for (const [index, device] of microphones.entries()) {
      if (device.deviceId === "default") continue;
      options.push(new Option(device.label || `麦克风 ${index + 1}`, device.deviceId));
    }
    ui.microphone.replaceChildren(...options);
    ui.microphone.value = options.some((option) => option.value === saved) ? saved : "";
  } catch {
    // Keep the system default entry.
  }
}

// ---------- level meter ----------

function drawMeter() {
  const canvas = ui.meter;
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (canvas.width !== Math.round(width * ratio)) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  const step = width / levels.length;
  const barWidth = Math.max(2, Math.min(5, step * 0.55));
  for (const [index, level] of levels.entries()) {
    const barHeight = Math.max(3, ((height - 5) * level) / 100);
    context.fillStyle = level > 3 ? "#51d6c4" : "#486078";
    const x = index * step + (step - barWidth) / 2;
    const y = (height - barHeight) / 2;
    context.beginPath();
    if (context.roundRect) context.roundRect(x, y, barWidth, barHeight, barWidth / 2);
    else context.rect(x, y, barWidth, barHeight);
    context.fill();
  }
}

function meterLoop() {
  if (!recorder) return;
  const level = recorder.readLevel();
  levels = [...levels.slice(1), level];
  ui.levelStatus.textContent = level > 5 ? "正在拾音" : "声音偏小 / 安静";
  drawMeter();
  setTimeout(meterLoop, 75);
}

// ---------- screen wake lock ----------

async function updateWakeLock() {
  const wanted = recorder !== null || jobs.some((job) => ["queued", "running", "retry"].includes(job.state));
  try {
    if (wanted && !wakeLock && "wakeLock" in navigator && document.visibilityState === "visible") {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => (wakeLock = null));
    } else if (!wanted && wakeLock) {
      await wakeLock.release();
    }
  } catch {
    // Wake lock is a convenience; recording works without it.
  }
}

// ---------- recording ----------

async function startRecording() {
  if (!apiKey()) {
    ui.settings.open = true;
    ui.keyInput.focus();
    setStatus("请先粘贴并保存 API key");
    return false;
  }
  if (!(await freshVocabulary())) return false;
  const take = new LocalRecorder(ui.microphone.value);
  ui.record.disabled = true;
  try {
    await take.start();
  } catch (error) {
    take.discard();
    ui.record.disabled = false;
    setStatus(error.name === "NotAllowedError" ? "没有麦克风权限，请在浏览器设置中允许" : `麦克风无法打开：${error.message}`);
    return false;
  }
  ui.record.disabled = false;
  recorder = take;
  session = { mode: ui.mode.value, vocabulary: [...vocabulary] };
  recordingStarted = performance.now();
  ui.record.textContent = "结束并提交";
  ui.record.classList.add("recording");
  ui.next.hidden = false;
  ui.discard.hidden = false;
  ui.mode.disabled = ui.microphone.disabled = true;
  setStatus(`正在录第 ${nextJobNumber} 段 · 仅在本设备，尚未发送`);
  levels.fill(0);
  meterLoop();
  updateWakeLock();
  navigator.storage?.persist?.();
  if (!ui.microphone.options[1]) refreshMicrophones(); // Labels appear once permission is granted.
  return true;
}

function elapsedSeconds() {
  return Math.floor((performance.now() - recordingStarted) / 1000);
}

function resetControls() {
  ui.record.textContent = "开始录音";
  ui.record.classList.remove("recording");
  ui.next.hidden = true;
  ui.discard.hidden = true;
  ui.mode.disabled = ui.microphone.disabled = false;
  ui.timer.textContent = `00:00 / ${clockText(recordingLimit())}`;
}

async function stopRecording() {
  const take = recorder;
  const seconds = elapsedSeconds();
  const { mode, vocabulary: terms } = session;
  recorder = null;
  resetControls();
  ui.levelStatus.textContent = "录音已结束";
  let audio;
  try {
    audio = await take.finish();
  } catch (error) {
    setStatus(`录音失败：${error.message}`);
    return false;
  }
  const job = await addJob({ audio, seconds, mode, vocabulary: terms, source: "录音" });
  setStatus(`第 ${job.number} 段已提交转写 · 可以直接录下一段`);
  return true;
}

function discardRecording() {
  if (!recorder) return;
  recorder.discard();
  recorder = null;
  resetControls();
  levels.fill(0);
  drawMeter();
  ui.levelStatus.textContent = "录音时显示";
  setStatus("已放弃这段录音 · 没有保存，也没有发送");
  updateWakeLock();
}

function tick() {
  if (!recorder) return;
  const elapsed = elapsedSeconds();
  const limit = recordingLimit();
  ui.timer.textContent = `${clockText(elapsed)} / ${clockText(limit)}`;
  if (limit - elapsed === 30) setStatus("还剩 30 秒，届时会自动结束录音并提交");
  if (elapsed >= limit) stopRecording();
}

async function importFiles(files) {
  if (!(await freshVocabulary())) return;
  for (const file of files) {
    try {
      const pcm = await decodeToPcm(file);
      const seconds = Math.round(pcm.length / SAMPLE_RATE);
      if (seconds > LIVE_RECORDING_SECONDS) {
        setStatus(`${file.name} 长 ${clockText(seconds)}，超过 ${clockText(LIVE_RECORDING_SECONDS)}，请剪短后再导入`);
        continue;
      }
      const job = await addJob({
        audio: file, seconds, mode: ui.mode.value, vocabulary: [...vocabulary], source: file.name,
      });
      setStatus(`已导入 ${file.name}，作为第 ${job.number} 段转写`);
    } catch (error) {
      setStatus(`无法读取 ${file.name}：${error.message}`);
    }
  }
}

// ---------- jobs ----------

function persist(job) {
  const { number, created, seconds, mode, vocabulary: terms, source, text, error, attempts, audio } = job;
  // An interrupted run resumes from the queue next time the page opens.
  const state = job.state === "running" || job.state === "retry" ? "queued" : job.state;
  return saveJob({ number, created, seconds, mode, vocabulary: terms, source, state, text, error, attempts, audio });
}

async function addJob({ audio, seconds, mode, vocabulary: terms, source }) {
  const job = {
    number: nextJobNumber++, created: Date.now(), seconds, mode, vocabulary: terms, source,
    state: "queued", text: "", error: "", attempts: 0, audio, progress: "", retryAt: 0, retryReason: "",
  };
  jobs.push(job);
  try {
    await persist(job);
  } catch (error) {
    job.error = `无法暂存录音（${error.message}）；请在转写完成前不要关闭页面。`;
  }
  renderJob(job);
  dispatchJobs();
  return job;
}

function dispatchJobs() {
  const now = Date.now();
  const running = jobs.filter((job) => job.state === "running").length;
  const waiting = jobs.filter((job) => job.state === "queued" || (job.state === "retry" && now >= job.retryAt));
  if (waiting.length && running < MAX_PARALLEL_JOBS && now >= cooldownUntil) {
    const key = apiKey();
    if (key) waiting.slice(0, MAX_PARALLEL_JOBS - running).forEach((job) => runJob(job, key));
    else setStatus("API key 未设置；保存 Key 后开始转写排队的录音");
  }
  refreshJobs();
  updateWakeLock();
}

async function runJob(job, key) {
  job.attempts += 1;
  job.state = "running";
  job.progress = "准备中…";
  renderJob(job);
  let text = "";
  try {
    const pcm = await decodeToPcm(job.audio);
    text = await transcribeLive({
      apiKey: key, mode: job.mode, vocabulary: job.vocabulary, pcm,
      onProgress: (progress) => {
        job.progress = progress;
        renderJob(job);
      },
    });
  } catch (error) {
    job.error = String(error?.message || error).replaceAll(key, "[hidden]") || "没有收到文字。";
  }
  if (text) {
    job.state = "done";
    job.text = text;
    job.error = "";
    job.audio = null; // The recording has served its purpose; drop it from this device.
    keyVerified = true;
    refreshKeyStatus();
    setStatus(`第 ${job.number} 段转写完成`);
  } else {
    const delay = retryDelay(job.error, job.attempts);
    if (delay === null) {
      job.state = "failed";
      setStatus(`第 ${job.number} 段转写失败；录音仍暂存在本设备，可点“重试”`);
    } else {
      job.state = "retry";
      job.retryAt = Date.now() + delay * 1000;
      if (QUOTA_ERROR.test(job.error)) {
        cooldownUntil = Math.max(cooldownUntil, job.retryAt);
        job.retryReason = "额度限流";
      } else {
        job.retryReason = "连接中断";
      }
      setStatus(`第 ${job.number} 段遇到${job.retryReason}，${delay} 秒后自动重试`);
    }
  }
  if (jobs.includes(job)) await persist(job).catch(() => {});
  renderJob(job);
  dispatchJobs();
}

function jobHeader(job) {
  const source = job.source === "录音" ? `录音 ${clockText(job.seconds)}` : `导入 ${clockText(job.seconds)} · ${job.source}`;
  return `第 ${job.number} 段 · ${timeOfDay(job.created)} · ${source} · ${job.mode}`;
}

function jobStateText(job, now) {
  if (job.state === "running") return job.progress;
  if (job.state === "retry") {
    return `${job.retryReason} · ${clockText(Math.max(0, Math.ceil((job.retryAt - now) / 1000)))} 后第 ${job.attempts + 1} 次尝试`;
  }
  if (job.state === "failed") return `× 转写失败：${job.error}`;
  if (job.state === "queued") {
    const cooling = cooldownUntil > now ? ` · 限流冷却 ${clockText(Math.ceil((cooldownUntil - now) / 1000))}` : " · 等待空位";
    return `排队中${cooling}${job.error ? `（上次：${job.error}）` : ""}`;
  }
  return "";
}

const saveTimers = new Map();

function buildJobElement(job) {
  const item = document.createElement("li");
  item.className = "job";
  item.innerHTML = `
    <div class="job-head muted"></div>
    <div class="job-state"></div>
    <textarea rows="4" hidden></textarea>
    <div class="job-actions">
      <button type="button" data-action="retry" hidden>重试</button>
      <button type="button" data-action="remove" hidden>移除</button>
      <button type="button" data-action="copy" hidden>复制本段</button>
    </div>`;
  const textarea = item.querySelector("textarea");
  textarea.addEventListener("input", () => {
    job.text = textarea.value;
    autoGrow(textarea);
    clearTimeout(saveTimers.get(job.number));
    saveTimers.set(job.number, setTimeout(() => persist(job).catch(() => {}), 500));
  });
  item.querySelector('[data-action="copy"]').addEventListener("click", async () => {
    await copyText(job.text);
    toast(`已复制第 ${job.number} 段`);
  });
  item.querySelector('[data-action="retry"]').addEventListener("click", () => {
    if (job.state === "failed") job.attempts = 0; // A manual retry gets a fresh set of automatic retries.
    job.state = "queued";
    cooldownUntil = 0;
    renderJob(job);
    dispatchJobs();
  });
  item.querySelector('[data-action="remove"]').addEventListener("click", () => {
    if (job.state === "failed" && !confirm(`删除第 ${job.number} 段？这段录音没有转写成功，删除后无法恢复。`)) return;
    removeJobs([job]);
  });
  ui.jobs.prepend(item);
  return item;
}

const elements = new Map();

function autoGrow(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${textarea.scrollHeight + 2}px`;
}

function renderJob(job, now = Date.now()) {
  let item = elements.get(job.number);
  if (!item) elements.set(job.number, (item = buildJobElement(job)));
  item.dataset.state = job.state;
  item.querySelector(".job-head").textContent = jobHeader(job);
  const state = item.querySelector(".job-state");
  state.textContent = jobStateText(job, now);
  state.classList.toggle("cooldown", job.state === "queued" && cooldownUntil > now);
  const textarea = item.querySelector("textarea");
  const done = job.state === "done";
  if (done && textarea.hidden) {
    textarea.value = job.text;
    textarea.hidden = false;
    requestAnimationFrame(() => autoGrow(textarea));
  }
  textarea.hidden = !done;
  item.querySelector('[data-action="copy"]').hidden = !done;
  const retry = item.querySelector('[data-action="retry"]');
  retry.hidden = !["retry", "failed"].includes(job.state);
  retry.textContent = job.state === "retry" ? "立即重试" : "重试";
  item.querySelector('[data-action="remove"]').hidden = !["done", "failed"].includes(job.state);
}

function refreshJobs() {
  const now = Date.now();
  jobs.filter((job) => job.state !== "done").forEach((job) => renderJob(job, now));
  const count = (states) => jobs.filter((job) => states.includes(job.state)).length;
  const waiting = count(["queued", "retry"]);
  let summary = `转写中 ${count(["running"])} · 排队 ${waiting} · 完成 ${count(["done"])}`;
  if (cooldownUntil > now && waiting) summary += ` · 限流冷却 ${clockText(Math.ceil((cooldownUntil - now) / 1000))}`;
  ui.queueSummary.textContent = jobs.length ? summary : "";
  ui.empty.hidden = jobs.length > 0;
  ui.copyAll.disabled = ui.clearDone.disabled = !jobs.some((job) => job.state === "done");
}

function removeJobs(removed) {
  for (const job of removed) {
    jobs = jobs.filter((other) => other !== job);
    clearTimeout(saveTimers.get(job.number));
    elements.get(job.number)?.remove();
    elements.delete(job.number);
    deleteJob(job.number).catch(() => {});
  }
  refreshJobs();
}

// ---------- events ----------

ui.keyForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const key = ui.keyInput.value.trim();
  if (!key) return;
  localStorage.setItem(KEY_STORAGE, key);
  ui.keyInput.value = "";
  keyVerified = false;
  refreshKeyStatus();
  setStatus("API key 已保存在这个浏览器，等待首次转写验证");
  dispatchJobs();
});

ui.forgetKey.addEventListener("click", () => {
  if (!confirm("从这个浏览器删除 API key？")) return;
  localStorage.removeItem(KEY_STORAGE);
  keyVerified = false;
  refreshKeyStatus();
  ui.settings.open = true;
});

ui.mode.addEventListener("change", () => {
  localStorage.setItem("mode", ui.mode.value);
  refreshSettingsSummary();
});
ui.microphone.addEventListener("change", () => localStorage.setItem("microphone", ui.microphone.value));

ui.record.addEventListener("click", () => {
  primeAudio();
  recorder ? stopRecording() : startRecording();
});
ui.next.addEventListener("click", async () => {
  primeAudio();
  if (recorder && (await stopRecording())) startRecording();
});
ui.discard.addEventListener("click", discardRecording);

ui.importFile.addEventListener("change", () => {
  importFiles([...ui.importFile.files]);
  ui.importFile.value = "";
});

ui.copyAll.addEventListener("click", async () => {
  const done = jobs.filter((job) => job.state === "done" && job.text.trim()).map((job) => job.text.trim());
  await copyText(done.join("\n\n"));
  toast(`已按录音顺序复制 ${done.length} 段`);
});

ui.clearDone.addEventListener("click", () => {
  const done = jobs.filter((job) => job.state === "done");
  if (done.length && confirm(`清除 ${done.length} 段已完成的文字？清除后无法恢复，需要的话请先复制。`)) {
    removeJobs(done);
    setStatus(`已清除 ${done.length} 段已完成的记录`);
  }
});

ui.showVocabulary.addEventListener("click", () => {
  ui.vocabularyFilter.value = "";
  ui.vocabularyEditor.value = drive.fileId ? (drive.loadedText ?? "") : vocabulary.join("\n");
  renderVocabulary();
  ui.vocabularyDialog.showModal();
});
ui.vocabularyFilter.addEventListener("input", renderVocabulary);
ui.saveVocabulary.addEventListener("click", async () => {
  try {
    const terms = parseVocabulary(ui.vocabularyEditor.value);
    if (drive.fileId) {
      const { clientId } = driveConfig();
      await drive.save(clientId, ui.vocabularyEditor.value);
    } else {
      localStorage.setItem(VOCABULARY_STORAGE, terms.join("\n"));
    }
    vocabulary = terms;
    if (!drive.fileId) ui.vocabularyEditor.value = terms.join("\n");
    showDriveState();
    renderVocabulary();
    toast(`已保存 ${terms.length} 个词条${drive.fileId ? "到 Drive" : "到本设备"}`);
  } catch (error) {
    toast(error.message);
    showDriveState();
  }
});

ui.drivePick.addEventListener("click", async () => {
  ui.drivePick.disabled = true;
  try {
    if (await drive.pick(driveConfig())) await readDriveVocabulary();
  } catch (error) {
    toast(error.message);
  } finally {
    ui.drivePick.disabled = false;
    showDriveState();
  }
});
ui.driveRefresh.addEventListener("click", async () => {
  ui.driveRefresh.disabled = true;
  try { await readDriveVocabulary(); }
  catch (error) { drive.loadedText = null; toast(error.message); }
  finally { ui.driveRefresh.disabled = false; showDriveState(); }
});
ui.driveDisconnect.addEventListener("click", () => {
  drive.disconnect();
  loadVocabulary();
  ui.vocabularyEditor.value = vocabulary.join("\n");
  renderVocabulary();
  showDriveState();
});

window.addEventListener("beforeunload", (event) => {
  if (recorder || jobs.some((job) => job.state === "running")) event.preventDefault();
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") updateWakeLock();
});
window.addEventListener("resize", drawMeter);

// ---------- start ----------

async function restoreJobs() {
  const saved = (await loadJobs()).sort((a, b) => a.number - b.number);
  for (const record of saved) {
    const job = { ...record, progress: "", retryAt: 0, retryReason: "" };
    if (job.state === "queued" && !job.audio) {
      job.state = "failed";
      job.error = "暂存的录音已丢失。";
    }
    jobs.push(job);
    renderJob(job);
  }
  nextJobNumber = (saved.at(-1)?.number ?? 0) + 1;
}

ui.mode.value = localStorage.getItem("mode") || "VERBATIM";
ui.timer.textContent = `00:00 / ${clockText(recordingLimit())}`;
refreshKeyStatus();
ui.settings.open = !apiKey();
drawMeter();
refreshMicrophones();
loadVocabulary();
ui.driveClientId.value = localStorage.getItem("drive-client-id") || "";
ui.driveApiKey.value = localStorage.getItem("drive-api-key") || "";
ui.driveProjectNumber.value = localStorage.getItem("drive-project-number") || "";
if (drive.fileId) vocabulary = [];
showDriveState();
try {
  await restoreJobs();
} catch (error) {
  setStatus(`无法读取本设备的转写记录：${error.message}`);
}
dispatchJobs();
setInterval(() => {
  tick();
  dispatchJobs();
}, 1000);
