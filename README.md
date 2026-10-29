# Gemini Live Dictation

Personal dictation with Gemini Live Transcription, in three forms: a Windows desktop app, a
browser app, and an Android voice keyboard. You record first; when you finish, the whole
recording is sent to `gemini-3.5-transcribe-live` and the final text comes back in one piece,
so it is not cut at every pause. Your personal vocabulary (names, technical terms) is sent with
each recording to improve accuracy. Each app uses your own Gemini API key; nothing goes
through a server of ours.

The apps show English by default and Simplified Chinese when the system or browser language
is Chinese.

## New computer: double-click install and start

Works on 64-bit Intel / AMD PCs with Windows 10 / 11:

1. Download the repository ZIP from GitHub and **extract it completely** into a folder you
   plan to keep; if you already cloned the repository, use that folder.
2. Double-click **`Install Dictation.cmd`**. The installer window shows its progress: it
   downloads a private Python runtime, installs the desktop and audio components, creates a
   desktop shortcut, and opens the app. The first install needs internet access and can take
   a few minutes; no commands, preinstalled Python, or administrator rights are needed.
3. Paste your own **Gemini API key** in the app and click "Save key". Each new
   computer needs the key again; the GitHub repository never carries keys from other
   computers.
4. To use a cloud vocabulary, install and sign in to **Google Drive for desktop**, then click
   "Choose vocabulary file" in the app and pick the `.txt` file Drive syncs to this computer.
   You can also start with a local vocabulary.

After that, double-click **Gemini Dictation** on the desktop or **`Start Dictation.cmd`** in
the project. If the runtime is missing when you click "Start", the installer opens first.
After updating the repository, double-click "Install" again to update dependencies; your key,
vocabulary, recordings, and transcripts are kept.

Keep the project folder: the shortcut points to the app inside it, so after moving the folder,
double-click Install again in the new location. Installer tools and download caches live in
`.runtime/`, the runtime in `.venv/`; neither is committed to Git. If installation fails, the
reason is shown and saved to `.runtime/install.log`; a program error at startup is shown and
saved to `.runtime/launch-error.log`.

The installer uses a pinned, SHA-256-verified
[official uv release](https://github.com/astral-sh/uv/releases/tag/0.12.19). uv downloads a
Python used only by this project and does not change the system PATH; see the
[uv docs](https://docs.astral.sh/uv/guides/install-python/). The first install needs access
to GitHub and PyPI.

On a phone, install the APK as described below; downloading the repository on a computer does
not install the keyboard. The web app also runs directly in a browser without installing
anything.

The Windows window, taskbar, installer, and desktop shortcut, the Android app, and the web
home-screen icon all use the same Psyduck icon. After an update, restart the desktop app and
run `Install Dictation.cmd` again to refresh the desktop shortcut; if a pinned taskbar icon
still shows the old icon, unpin it and pin the new app again. Android needs the new APK. For
the web app, publish the new version; if a home-screen shortcut keeps the old icon, delete it
and add it again.

The original icon is `gemini_live_dictation/assets/app-icon.png`. The Windows ICO, the Android
density-specific and adaptive icons, and the web icons are generated from it by
`scripts/generate-icons.py`; after changing the original, run the script in a development
environment with Pillow. The apps do not need Pillow at runtime.

## Android voice keyboard (test build)

`android/` contains an Android system keyboard (input method): tap the microphone in any app's
text field, speak, and the transcript is typed in. The vocabulary is a single `.txt` file in
Google Drive that you grant read/write access to through the Android file picker. The Gemini
API key is pasted once (e.g. from Bitwarden) and stored encrypted on the phone. Installation
and usage: [Android guide](android/README.md).

## Desktop app (Windows)

The desktop app has a local window. Choose `VERBATIM` or `SMART`, pick a microphone, click
"Start recording", speak, then click "Stop and submit". While recording, audio stays
on this computer: nothing is sent to Gemini and no partial transcript is shown. The default
model is `gemini-3.5-transcribe-live`: after you stop, the app turns off Live's automatic
segmentation and sends the whole recording as one "utterance" at about 4× speed (Live itself
processes at about 4×; sending faster lets unprocessed audio pile up, and past roughly 80
seconds of backlog the server disconnects). Live then returns the final text using the context
of the whole recording, instead of finalizing each pause on its own. In practice the wait is
about a quarter of the recording length (a 1-minute recording takes about 15–20 seconds). The
level meter only shows the local microphone input level.

You don't have to wait for a transcript before recording again: click
"Stop and submit" and start the next clip right away, or click
"Submit, record next" while recording to do both in one step. If you don't like a clip,
click "× Discard": the recording is dropped, not saved locally, and not sent to
Gemini.

Each clip is a separate transcription job. The list on the left shows queued, sending
progress, done, or a retry countdown. Up to 3 clips are transcribed at once; the rest wait in
recording order. Live's concurrent-session quota is not documented: opening 10 sessions at
once with one key, 5–8 were accepted and the rest immediately returned
`1011 … exceeded your current quota`, recovering after a short wait. So when rate-limited, the
app stops starting new jobs and retries automatically after the delay the server suggests (or
10 / 30 / 60 seconds when it gives none), up to 4 times. Dropped connections are retried as
well. Errors that retrying cannot fix, such as an invalid API key, are marked failed right
away and can be retried by hand with "Retry".

Select any clip to view, edit, or "Copy" it; "Copy all" joins all
finished clips in recording order. When the list gets long, select several and click
"Remove selected" (or press Delete), or click "Clear finished". This only removes
them from the list: files in `transcripts/` are not deleted, and text you edited in the window
is written back to its transcript file first. Clips that are transcribing or queued cannot be
removed.

Each transcript is also saved locally in `transcripts/`. Recordings are kept in `recordings/`
until they are transcribed; once the text is written to `transcripts/`, the recording is
deleted automatically. Recordings that failed to transcribe are kept so they can be retried.
Both folders are ignored by Git.

The double-click install above is recommended. If you already have a Python virtual
environment, you can install the desktop app by hand for development:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[gui]"
```

Afterwards, double-click `Start Dictation.cmd` in the project, or run:

```powershell
.\.venv\Scripts\gemini-live-gui.exe
```

If a `.env` file exists, the window shows that the key was read from this computer; it shows
it as verified only after a successful transcription. You can also paste a new key in the
window and click "Save key". Click "View / edit vocabulary" to review and edit
the selected `.txt` vocabulary file: terms are listed alphabetically in columns, typing a new
term filters the list and warns about duplicates (case-insensitive), double-click a term to
edit it, and select one and press Delete to remove it. When transcribing, the audio and the
terms in your personal vocabulary are sent to Gemini.

Click "Choose vocabulary file" to use the `.txt` file that Google Drive for desktop syncs to
this computer. Before saving, the app checks whether another device changed the file; it is
read again every time you start recording. The desktop app uses Drive for desktop's synced
file path, so Drive for desktop must be installed and signed in. The vocabulary file is never
published with the website.

The microphone list only shows devices that can currently be opened, with duplicate driver
entries merged. `VERBATIM` is selected by default because it keeps the corrections you make
while speaking; switch to `SMART` for tidier text. A single Live session is officially limited
to 10 minutes, so the app limits a recording to 8 minutes 30 seconds and stops automatically,
leaving room for connecting and finishing. Record longer content in several clips, or switch
to the regular `gemini-3.5-transcribe`, which this app allows up to 20 minutes per recording,
though regular requests count against your account's quota. Live's free tier currently shows
no per-minute or per-day request limit, but it is still subject to per-minute token limits and
the session length limit.

## Web app (phones, tablets, any browser)

`web/` is the same workflow as a web page, with nothing to install: record on the device,
then hand the whole recording to Live. You can record several clips in a row; queueing and
rate-limit retries work as in the desktop app. The site is plain static files; pushing to
`main` publishes it to GitHub Pages through GitHub Actions.

Click "View / edit vocabulary" to edit your personal vocabulary in the page. Without Google
Drive, the vocabulary is stored in the current browser (terms cached by older versions are
moved into it the first time the new version opens). With Drive connected, the page reads the
selected `.txt` file before every recording or audio import and can save your edits back to
the same file. The browser needs permission again each time it is reopened; if authorization
fails, it will not quietly transcribe with an old vocabulary.

### Connecting the web app to a Google Drive vocabulary

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a project, enable
   the **Google Drive API** and **Google Picker API**, and configure the OAuth consent screen;
   while the app is in testing, add your own Google account as a test user.
2. Create a **Web application** OAuth client and add the page's origin (e.g.
   `https://<username>.github.io`, plus `http://localhost:8765` for local preview) to
   Authorized JavaScript origins. Create an API key and note the Cloud **project number**. If
   you restrict the API key by website, allow both the page's origin and
   `https://docs.google.com/*`, as the Google Picker docs describe; if you restrict it by API,
   allow the Picker API and the Drive API.
3. In the page, open "View / edit vocabulary → Connect a Google Drive vocabulary", enter the
   client ID, API key, and project number, click "Choose Drive vocabulary", sign in, and select
   the same `.txt` file the Android keyboard uses. The scope is `drive.file`. This public web
   configuration is stored in the current browser; the access token stays in page memory and
   is never uploaded to this site's server.
4. If you already have a browser-local vocabulary, copy its terms into the Drive file before
   switching; switching does not merge the two. Before saving, the page compares the file in
   Drive and asks you to reload it if another device changed it, instead of overwriting.
   Reading and writing through the Google Drive API is not one atomic transaction, so writes
   within a very short time of each other can still conflict.

Google's documentation: [web authorization](https://developers.google.com/identity/oauth2/web/guides/use-token-model),
[Google Picker](https://developers.google.com/workspace/drive/picker/guides/web-picker),
[updating Drive files](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/update).

- **API key**: the page's code contains no key. Paste it once on each device (e.g. from
  Bitwarden); it is kept only in that browser's local storage. Audio goes straight from the
  device to Google with no other server in between.
- **Recordings**: kept in the browser (IndexedDB) only until they are transcribed, then
  deleted automatically. Failed clips keep their recording so they can be retried; "Remove"
  deletes it. Discarded clips are not saved.
- **Text**: stays in this browser; you can edit it, "Copy clip" or "Copy all", and
  "Clear finished" deletes it.
- "Import audio" transcribes recording files already on the device (up to 08:30 each).
- Keep the page in the foreground while transcribing: when the phone locks or you switch apps,
  the browser may pause recording and sending.
- In Safari on iPhone / iPad, choose "Share → Add to Home Screen" to open it like an app.

The first deployment needs two settings on GitHub: make the repository public (Pages requires
this on free accounts), and under Settings → Pages → Build and deployment, set Source to
**GitHub Actions**. The address is `https://<username>.github.io/gemini-dictation/`.

Local preview: in the project root run
`.\.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1`, then open
`http://localhost:8765/web/` (the microphone works on localhost).

## Command-line Live experiment

The separate command-line tool streams microphone audio to `gemini-3.5-transcribe-live`.
It streams 16 kHz mono microphone audio to Gemini and prints both interim and finalized
transcription. It can also stream a saved audio file at playback speed. Automatic language
detection supports Chinese-English code switching.

## Privacy defaults

- The API key is read from `GEMINI_API_KEY`, optionally via an ignored `.env` file.
- `transcripts/` and `recordings/` are ignored by Git.
- `config/vocabulary.txt` is ignored by Git and no longer included in the web deployment.
- The web vocabulary is stored locally in each browser unless a Drive file is selected. Earlier commits and deployments may still contain the old vocabulary; removing it from the current site does not erase Git history or caches.
- No API key, audio recording, or transcript is included in source control.

## Setup (Windows PowerShell)

Use the same Python 3.13 installation that you normally use. This project creates a separate
environment, so it does not change its global NumPy/SciPy packages:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
Copy-Item .env.example .env
```

Put your Gemini API key in `.env`. Do not paste it into a terminal transcript, source file, or
commit.

If PowerShell blocks activation, run this once for the current window only:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

## Vocabulary

If `config/vocabulary.txt` does not exist yet, create it from the example:

```powershell
Copy-Item config\vocabulary.example.txt config\vocabulary.txt
```

Add one term or phrase per line. The file stays local and is ignored by Git. Gemini accepts
up to 1,000 terms; Google recommends keeping the active list to roughly 100 or fewer.

## Run

```powershell
gemini-live-dictation --vocabulary config\vocabulary.txt --mode SMART --output transcripts\session.txt
```

Press `Ctrl+C` to stop. `SMART` removes fillers and applies readable punctuation; use
`--mode VERBATIM` for a literal test transcript. To inspect available microphones:

```powershell
gemini-live-dictation --list-devices
```

For a 15-second connection test, add `--duration 15`. Every run stops automatically after
at most 540 seconds, below the Live Transcription 10-minute session limit. Run the command
again to start a new session; the `--output` file can be reused to append final segments.

To compare Live with a previous transcription of a saved recording:

```powershell
gemini-live-dictation --file "C:\path\to\recording.m4a" --vocabulary config\vocabulary.txt --mode SMART --output transcripts\live-test.txt
```

The file is decoded locally and streamed at its original speed. Only final text is saved;
the audio file is never copied into this repository.

## Next steps

1. Try a short recording on a real Windows computer to confirm the microphone and
   transcription work.
2. Verify installation, keyboard switching, voice input, and Drive vocabulary reads and writes
   on a physical Android phone.
3. Tune the personal vocabulary and check that it syncs across devices.
