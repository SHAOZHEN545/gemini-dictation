// Local-only recording: nothing leaves the device until finish() hands back the audio.

const MIME_TYPES = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"];
let meterContext = null;

/** Call synchronously inside a click: mobile browsers only let audio contexts run from a gesture. */
export function primeAudio() {
  meterContext ??= new AudioContext();
  if (meterContext.state === "suspended") meterContext.resume().catch(() => {});
}

export async function listMicrophones() {
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices.filter((device) => device.kind === "audioinput" && device.deviceId);
}

export class LocalRecorder {
  constructor(deviceId) {
    this.deviceId = deviceId;
    this.chunks = [];
    this.level = 0;
  }

  async start() {
    // Raw input like the desktop recorder; call-style processing only colours the audio Gemini hears.
    const audio = { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false };
    if (this.deviceId) audio.deviceId = { exact: this.deviceId };
    this.stream = await navigator.mediaDevices.getUserMedia({ audio });
    try {
      const mimeType = MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type));
      this.recorder = new MediaRecorder(this.stream, mimeType ? { mimeType } : undefined);
      this.recorder.ondataavailable = (event) => event.data.size && this.chunks.push(event.data);
      this.recorder.start(1000);
      primeAudio();
      this.analyser = meterContext.createAnalyser();
      this.analyser.fftSize = 1024;
      this.source = meterContext.createMediaStreamSource(this.stream);
      this.source.connect(this.analyser);
      this.samples = new Float32Array(this.analyser.fftSize);
    } catch (error) {
      this._release();
      throw error;
    }
  }

  /** Current input level on the desktop meter's 0-100 scale (-55 dBFS to -8 dBFS). */
  readLevel() {
    if (!this.analyser) return 0;
    this.analyser.getFloatTimeDomainData(this.samples);
    let sum = 0;
    for (const sample of this.samples) sum += sample * sample;
    const dbfs = 20 * Math.log10(Math.max(Math.sqrt(sum / this.samples.length), 1e-6));
    this.level = Math.max(0, Math.min(100, Math.round(((dbfs + 55) * 100) / 47)));
    return this.level;
  }

  get deviceLabel() {
    return this.stream?.getAudioTracks()[0]?.label || "";
  }

  _release() {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.source?.disconnect();
    this.source = null;
    this.analyser = null;
  }

  /** Stop without keeping anything, so this take is neither stored nor sent. */
  discard() {
    if (this.recorder && this.recorder.state !== "inactive") {
      this.recorder.ondataavailable = null;
      this.recorder.stop();
    }
    this.chunks = [];
    this._release();
  }

  finish() {
    return new Promise((resolve, reject) => {
      this.recorder.onstop = () => {
        this._release();
        const blob = new Blob(this.chunks, { type: this.recorder.mimeType || "audio/webm" });
        this.chunks = [];
        blob.size ? resolve(blob) : reject(new Error("没有录到声音，请检查麦克风权限。"));
      };
      this.recorder.stop();
    });
  }
}
