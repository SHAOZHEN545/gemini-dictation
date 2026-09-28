// Jobs live in this browser's IndexedDB: audio only until its transcript arrives, text until cleared.

const DB_NAME = "gemini-dictation";
const STORE = "jobs";

let opening = null;

function open() {
  opening ??= new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: "number" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return opening;
}

async function run(mode, action) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE, mode);
    const request = action(transaction.objectStore(STORE));
    transaction.oncomplete = () => resolve(request?.result);
    transaction.onerror = () => reject(transaction.error);
  });
}

export const loadJobs = () => run("readonly", (store) => store.getAll());
export const saveJob = (job) => run("readwrite", (store) => store.put(job));
export const deleteJob = (number) => run("readwrite", (store) => store.delete(number));
