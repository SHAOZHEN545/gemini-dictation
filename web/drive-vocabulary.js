import { t } from "./i18n.js";

const SCOPE = "https://www.googleapis.com/auth/drive.file";
const MAX_BYTES = 256_000;
const DRIVE_API = "https://www.googleapis.com/drive/v3/files";

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.onload = resolve;
    script.onerror = () => reject(new Error(t("drive.scriptFailed")));
    document.head.append(script);
  });
}

const libraries = Promise.all([
  loadScript("https://accounts.google.com/gsi/client"),
  loadScript("https://apis.google.com/js/api.js"),
]).then(() => new Promise((resolve, reject) => {
  if (!window.gapi || !window.google?.accounts?.oauth2) {
    reject(new Error(t("drive.libraryUnavailable")));
    return;
  }
  window.gapi.load("picker", { callback: resolve, onerror: () => reject(new Error(t("drive.pickerFailed"))) });
}));

export class DriveVocabulary {
  constructor() {
    this.token = null;
    this.expiresAt = 0;
    this.fileId = localStorage.getItem("drive-vocabulary-id") || "";
    this.fileName = localStorage.getItem("drive-vocabulary-name") || "";
    this.loadedText = null;
    this.client = null;
    this.clientId = "";
  }

  async authorize(clientId) {
    await libraries;
    if (this.token && Date.now() < this.expiresAt - 60_000) return this.token;
    if (this.clientId !== clientId) {
      this.client = window.google.accounts.oauth2.initTokenClient({
        client_id: clientId,
        scope: SCOPE,
        callback: () => {},
        error_callback: () => {},
      });
      this.clientId = clientId;
    }
    return new Promise((resolve, reject) => {
      this.client.callback = (response) => {
        if (response.error || !response.access_token || !window.google.accounts.oauth2.hasGrantedAllScopes(response, SCOPE)) {
          reject(new Error(response.error_description || t("drive.permissionDenied")));
          return;
        }
        this.token = response.access_token;
        this.expiresAt = Date.now() + Number(response.expires_in || 3600) * 1000;
        resolve(this.token);
      };
      this.client.error_callback = () => reject(new Error(t("drive.popupClosed")));
      this.client.requestAccessToken({ prompt: this.token ? "" : "consent" });
    });
  }

  async pick({ clientId, apiKey, projectNumber }) {
    const token = await this.authorize(clientId);
    return new Promise((resolve, reject) => {
      const picker = new window.google.picker.PickerBuilder()
        .addView(new window.google.picker.DocsView().setMode(window.google.picker.DocsViewMode.LIST))
        .setOAuthToken(token)
        .setDeveloperKey(apiKey)
        .setAppId(projectNumber)
        .setCallback((data) => {
          const action = data[window.google.picker.Response.ACTION];
          if (action === window.google.picker.Action.CANCEL) { resolve(false); return; }
          if (action !== window.google.picker.Action.PICKED) return;
          const file = data[window.google.picker.Response.DOCUMENTS]?.[0];
          if (!file) { reject(new Error(t("drive.noFile"))); return; }
          const id = file[window.google.picker.Document.ID];
          const name = file[window.google.picker.Document.NAME] || t("drive.defaultFileName");
          if (!name.toLowerCase().endsWith(".txt")) { reject(new Error(t("drive.notTxt"))); return; }
          this.fileId = id;
          this.fileName = name;
          this.loadedText = null;
          localStorage.setItem("drive-vocabulary-id", id);
          localStorage.setItem("drive-vocabulary-name", name);
          resolve(true);
        })
        .build();
      picker.setVisible(true);
    });
  }

  disconnect() {
    this.fileId = "";
    this.fileName = "";
    this.loadedText = null;
    localStorage.removeItem("drive-vocabulary-id");
    localStorage.removeItem("drive-vocabulary-name");
  }

  async request(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: { Authorization: `Bearer ${this.token}`, ...options.headers },
    });
    if (!response.ok) {
      if (response.status === 401) { this.token = null; this.expiresAt = 0; }
      throw new Error(t(response.status === 401 ? "drive.requestUnauthorized" : "drive.requestFailed", { status: response.status }));
    }
    return response;
  }

  async read(clientId) {
    if (!this.fileId) throw new Error(t("drive.noFileChosen"));
    await this.authorize(clientId);
    const metadata = await this.request(`${DRIVE_API}/${encodeURIComponent(this.fileId)}?fields=id,name,mimeType,size`);
    const file = await metadata.json();
    if (file.mimeType !== "text/plain") throw new Error(t("drive.notPlainText"));
    if (Number(file.size || 0) > MAX_BYTES) throw new Error(t("drive.tooLarge"));
    const response = await this.request(`${DRIVE_API}/${encodeURIComponent(this.fileId)}?alt=media`);
    const bytes = await response.arrayBuffer();
    if (bytes.byteLength > MAX_BYTES) throw new Error(t("drive.tooLarge"));
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    this.loadedText = text;
    this.fileName = file.name;
    localStorage.setItem("drive-vocabulary-name", file.name);
    return text;
  }

  async save(clientId, text) {
    if (this.loadedText === null) throw new Error(t("drive.reloadBeforeSave"));
    if (new TextEncoder().encode(text).byteLength > MAX_BYTES) throw new Error(t("drive.tooLarge"));
    const expected = this.loadedText;
    const current = await this.read(clientId);
    if (current !== expected) {
      this.loadedText = null;
      throw new Error(t("drive.changedElsewhere"));
    }
    await this.request(`https://www.googleapis.com/upload/drive/v3/files/${encodeURIComponent(this.fileId)}?uploadType=media`, {
      method: "PATCH",
      headers: { "Content-Type": "text/plain; charset=utf-8" },
      body: text,
    });
    this.loadedText = text;
  }
}
