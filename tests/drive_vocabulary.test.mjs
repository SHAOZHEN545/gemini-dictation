import assert from "node:assert/strict";
import test from "node:test";

const storage = new Map();
globalThis.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
};

globalThis.document = {
  createElement: () => ({ onload: null }),
  head: { append: (script) => queueMicrotask(() => script.onload()) },
};

const tokenClient = {
  callback: null,
  requestAccessToken() { this.callback({ access_token: "test-token", expires_in: 3600 }); },
};
globalThis.window = {
  gapi: { load: (_, { callback }) => callback() },
  google: { accounts: { oauth2: {
    initTokenClient: () => tokenClient,
    hasGrantedAllScopes: () => true,
  } } },
};

const { DriveVocabulary } = await import("../web/drive-vocabulary.js");

test("reads the selected plain text file and saves an unchanged version", async () => {
  let remote = "# note\nalpha\n";
  let patches = 0;
  globalThis.fetch = async (url, options = {}) => {
    assert.equal(options.headers.Authorization, "Bearer test-token");
    if (url.includes("upload/drive") && options.method === "PATCH") {
      patches += 1;
      remote = options.body;
      return { ok: true };
    }
    if (url.includes("alt=media")) return { ok: true, arrayBuffer: async () => new TextEncoder().encode(remote).buffer };
    return { ok: true, json: async () => ({ name: "terms.txt", mimeType: "text/plain", size: remote.length }) };
  };

  const drive = new DriveVocabulary();
  drive.fileId = "file-123";
  assert.equal(await drive.read("client-id"), "# note\nalpha\n");
  await drive.save("client-id", "# note\nalpha\nbeta\n");
  assert.equal(remote, "# note\nalpha\nbeta\n");
  assert.equal(patches, 1);
});

test("refuses to overwrite a file changed by another device", async () => {
  let remote = "alpha\n";
  let patches = 0;
  globalThis.fetch = async (url, options = {}) => {
    if (options.method === "PATCH") { patches += 1; return { ok: true }; }
    if (url.includes("alt=media")) return { ok: true, arrayBuffer: async () => new TextEncoder().encode(remote).buffer };
    return { ok: true, json: async () => ({ name: "terms.txt", mimeType: "text/plain", size: remote.length }) };
  };

  const drive = new DriveVocabulary();
  drive.fileId = "file-123";
  await drive.read("client-id");
  remote = "alpha\nother-device\n";
  await assert.rejects(drive.save("client-id", "alpha\nmy-edit\n"), /其他设备修改/);
  assert.equal(patches, 0);
  assert.equal(drive.loadedText, null);
});
