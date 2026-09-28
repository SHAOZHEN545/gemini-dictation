// Whole-recording transcription through the Gemini Live WebSocket, mirroring the desktop
// LiveReplayWorker: one manual activity wraps the full recording, paced at Live's ~4x speed.

export const SAMPLE_RATE = 16_000;
export const LIVE_RECORDING_SECONDS = 510; // 8m30s leaves room within the 10-minute session.
const MODEL = "models/gemini-3.5-transcribe-live";
const ENDPOINT =
  "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent";
const CHUNK_SAMPLES = 1_600; // 100 ms of audio per message.
const LIVE_FINALIZATION_SECONDS = 30;
// Live closes the connection once about 80-90 s of unprocessed audio piles up, so send a short
// head start, then pace at the processing speed (see LIVE_SEND_SPEED in gui.py).
const LIVE_SEND_SPEED = 4;
const LIVE_HEAD_START_SECONDS = 15;
const LIVE_PROCESSING_SPEED = 4;

export function clockText(seconds) {
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

/** Join Live final chunks without treating chunk boundaries as paragraphs. */
export function joinLiveTranscripts(parts) {
  let result = "";
  for (let part of parts) {
    part = part.trim();
    if (!part) continue;
    if (result && /^[A-Za-z0-9]/.test(part) && /[A-Za-z0-9.!?,:;]$/.test(result)) result += " ";
    result += part;
  }
  return result;
}

/** Decode any recorded or imported audio to 16 kHz mono signed 16-bit PCM. */
export async function decodeToPcm(blob) {
  const context = new OfflineAudioContext(1, 1, SAMPLE_RATE);
  // decodeAudioData resamples to the context's rate, so the buffer arrives at 16 kHz.
  const buffer = await context.decodeAudioData(await blob.arrayBuffer());
  const pcm = new Int16Array(buffer.length);
  const channels = Array.from({ length: buffer.numberOfChannels }, (_, i) => buffer.getChannelData(i));
  for (let i = 0; i < buffer.length; i++) {
    let sample = 0;
    for (const channel of channels) sample += channel[i];
    sample = Math.max(-1, Math.min(1, sample / channels.length));
    pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  if (!pcm.length) throw new Error("录音里没有声音数据。");
  return pcm;
}

function base64(samples) {
  const bytes = new Uint8Array(samples.buffer, samples.byteOffset, samples.byteLength);
  return btoa(String.fromCharCode(...bytes));
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Send a finished recording to Live and resolve with the final transcript.
 * onProgress receives short status strings for the job list.
 */
export async function transcribeLive({ apiKey, mode, vocabulary, pcm, onProgress }) {
  const audioSeconds = pcm.length / SAMPLE_RATE;
  if (audioSeconds > LIVE_RECORDING_SECONDS + 2) {
    throw new Error(`这段录音 ${clockText(audioSeconds)}，超过 Live 安全时长 ${clockText(LIVE_RECORDING_SECONDS)}，请分段录制。`);
  }
  onProgress("连接中…");
  const socket = new WebSocket(`${ENDPOINT}?key=${encodeURIComponent(apiKey)}`);
  socket.binaryType = "arraybuffer";
  const decoder = new TextDecoder();
  const finalParts = [];
  let closed = null; // Error describing why the socket closed, once it has.
  let setupDone, generationDone;
  const setupComplete = new Promise((resolve) => (setupDone = resolve));
  const generationComplete = new Promise((resolve) => (generationDone = resolve));

  socket.onmessage = (event) => {
    const message = JSON.parse(typeof event.data === "string" ? event.data : decoder.decode(event.data));
    if (message.setupComplete) setupDone();
    const content = message.serverContent;
    const text = content?.inputTranscription?.text;
    if (text) finalParts.push(text.trim());
    if (content?.generationComplete) generationDone();
  };
  socket.onclose = (event) => {
    const reason = (event.reason || "").replaceAll(apiKey, "[hidden]");
    closed = new Error(`Live 连接关闭（${event.code}）${reason ? "：" + reason : ""}`);
    setupDone();
    generationDone();
  };

  try {
    await new Promise((resolve, reject) => {
      socket.onopen = resolve;
      socket.onerror = () => setTimeout(() => reject(closed || new Error("Live 连接失败（connection）。")), 50);
    });
    socket.onerror = null;
    socket.send(
      JSON.stringify({
        setup: {
          model: MODEL,
          generationConfig: { responseModalities: ["TEXT"] },
          inputAudioTranscription: { languageCodes: [], customVocabulary: vocabulary, mode },
          realtimeInputConfig: { automaticActivityDetection: { disabled: true } },
        },
      }),
    );
    await setupComplete;
    if (closed) throw closed;

    const estimate = Math.ceil(audioSeconds / LIVE_PROCESSING_SPEED) + 5;
    socket.send(JSON.stringify({ realtimeInput: { activityStart: {} } }));
    const started = performance.now();
    let reported = -5;
    for (let offset = 0; offset < pcm.length; offset += CHUNK_SAMPLES) {
      const percent = Math.floor((offset * 100) / pcm.length);
      if (percent >= reported + 5) {
        reported = percent;
        const remaining = Math.max(0, Math.round(estimate - (performance.now() - started) / 1000));
        onProgress(`发送中 ${percent}% · 约剩 ${clockText(remaining)}`);
      }
      if (closed) throw new Error(`${closed.message}；Live 连接在录音发送完之前关闭。`);
      const position = offset / SAMPLE_RATE;
      const sendAt = started + (Math.max(0, position - LIVE_HEAD_START_SECONDS) / LIVE_SEND_SPEED) * 1000;
      const wait = sendAt - performance.now();
      if (wait > 0) await sleep(wait);
      socket.send(
        JSON.stringify({
          realtimeInput: {
            audio: { data: base64(pcm.subarray(offset, offset + CHUNK_SAMPLES)), mimeType: `audio/pcm;rate=${SAMPLE_RATE}` },
          },
        }),
      );
    }
    socket.send(JSON.stringify({ realtimeInput: { activityEnd: {} } }));
    onProgress("录音已送完，等待定稿…");
    const timeout = (LIVE_FINALIZATION_SECONDS + (audioSeconds / LIVE_PROCESSING_SPEED) * 2) * 1000;
    await Promise.race([generationComplete, sleep(timeout)]);
  } finally {
    socket.onclose = null;
    if (socket.readyState <= WebSocket.OPEN) socket.close();
  }
  const text = joinLiveTranscripts(finalParts);
  if (!text) throw closed || new Error("Live 没有返回最终文字。");
  return text;
}
