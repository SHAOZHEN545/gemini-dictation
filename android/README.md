# Android voice keyboard (first test build)

This is an Android system keyboard (input method), not a home-screen web shortcut. Tap the
microphone to start recording and tap again to stop; the whole recording is kept on the phone,
then sent to Gemini Live, and the final text is typed into the current text field. Voice input
is disabled in password fields. The keyboard shows English, or Chinese when the phone's
language is Chinese.

After you stop recording, you can lock the screen, put the phone down, pull down the
notification shade, or switch to another app: transcription continues in the background and
the notification shade shows "Transcribing recording". If the screen turns off or the keyboard
hides while you are still recording, the recording ends at that moment and is transcribed as
usual, so nothing you already said is lost; tap "× Drop recording" if you don't want the clip.
When it finishes, if you are still in the same text field (for example you only pulled down
the notification shade), the text is typed in directly. If you locked the screen or switched
fields in between, the keyboard shows "Transcript ready, not typed yet" with the start of the
text; tap "Insert text" to type it into the current field. If transcription fails, the
recording stays on the phone: tap "↻ Retry" to send it again without re-recording. You can
record the next clip once the previous one is handled (inserted, retried successfully, or
discarded by tapping "Discard" twice).

## Build and install

When Android changes are pushed to `main`, the **Build Android keyboard** GitHub Actions
workflow builds an installable debug APK and publishes `app-debug.apk` as a standalone file on
a GitHub pre-release. On the phone, tap the APK to download it; no unzipping needed. The APK
is for personal testing: there is no release-signed build yet, and it has not been verified on
a physical phone. Each build is signed with a fresh debug key, so if Android refuses to update
over an older install ("conflicts with an existing package"), uninstall the old version first;
you will need to save the key, pick the vocabulary file, and enable the keyboard again.

For local development, install Android SDK 35, JDK 17, and Gradle 8.9, then run
`gradle :app:assembleDebug` in `android/`. You can also run
`gradle :wrapper --gradle-version 8.9` to generate a Gradle Wrapper and open the project in
Android Studio. The project uses Android Gradle Plugin 8.7.0. The APK is in
`app/build/outputs/apk/debug/`.

After installing, open "Gemini Voice Input":

1. Allow the microphone, paste your own Gemini API key (e.g. from Bitwarden), and save it. The
   key is encrypted with the Android Keystore and stays on the phone; it is never uploaded to
   Drive.
2. Optionally upload `config/vocabulary.txt` from your computer to Google Drive first. Tap
   "Choose existing vocabulary file" or "Create vocabulary file in Google Drive", open Google
   Drive in the system file picker, and choose the location and `.txt` file. The app keeps
   long-term read/write access to that one file and never needs access to your whole Drive.
3. Type one new term at a time and tap "Add & save" (or the keyboard's Done key). It appears
   immediately in the list, sorted alphabetically and by pinyin, and is written to the
   selected Drive file; the "Vocabulary saved to Drive" message confirms it. If saving fails,
   the reason is shown and your changes stay in the list; tap "Save changes to Drive" to
   retry. A save that has started keeps running after you leave the settings screen. The list
   has a permanent scrollbar: swipe the list or drag the handle on the right to move quickly.
   Tap a term to edit it and long-press to delete it; after editing or deleting, tap "Save
   changes to Drive" (adding a term also saves earlier changes). Comment lines starting with
   `#` in the file are kept. If another device changed the file, saving asks you to reload it
   first instead of overwriting. The keyboard reads the file again after every recording.
4. Tap "Enable Gemini Voice Input", then "Choose current keyboard". Tap the microphone in any
   normal text field to use it.

The tabs at the top of the keyboard switch between "Voice", "Edit", and "Clipboard":

- **Edit** has cursor keys, "Select all", "Copy", and "Paste" (pastes the most recent copy).
  Password fields cannot be copied from.
- **Clipboard** lists recently copied text, newest first, up to 50 items; beyond that the
  oldest is dropped. It collects text copied in any app while this keyboard is in use, text
  copied with "Copy" on the Edit tab, and every transcript after it is typed in, so if text
  lands in the wrong place or gets deleted you can get it back here. Copying the same text
  again moves it to the top. Tap an item to paste it into the current field, long-press to
  delete it; "Clear" needs a second tap to confirm. Copies that password managers (such as
  Bitwarden) mark as sensitive are never recorded. If an item shouldn't be kept, long-press to
  delete it; if the system clipboard still holds that text, it is cleared too. The history is
  stored only in the app's private storage: it is not uploaded or backed up, and a single copy
  over 100,000 characters is not recorded.
- To use a regular keyboard, tap the globe key to switch back to, e.g., the Samsung keyboard.

Until a recording is transcribed, it is kept in the app's private storage (other apps cannot
read it, and it is excluded from backups); the transcript is kept there too until it is typed
in. Once the text is typed in or you tap "Discard", both are deleted (a typed transcript is
also kept in the clipboard history, where you can long-press to delete it). The vocabulary is
only read and written through the file you picked; if Drive is temporarily unavailable,
transcription reports a read failure; try again once you are back online. The voice mode is
currently fixed to `VERBATIM`, and a clip can be up to 8 minutes 30 seconds long.

After you stop recording, the keyboard shows a progress bar, the current stage (reading the
vocabulary, connecting to Gemini, sending audio, waiting for the transcript), and the time
waited so far, and the total time when done. During sending, the percentage is the share of
audio handed to the network send queue; while the queue is still draining, it keeps showing
the upload status. Reading, connecting, and Gemini processing show an indeterminate progress
bar without estimating overall completion. Audio is only connected and sent after recording
ends: the first 15 seconds are submitted quickly and the rest at about 4× recording speed, so
longer recordings still involve a real wait.

The web app can read the same file through Google sign-in and the Drive file picker; first set
up Google Cloud web credentials as described in the root README. The Windows desktop app can
use the copy of this `.txt` file that Google Drive for desktop syncs to the computer.
