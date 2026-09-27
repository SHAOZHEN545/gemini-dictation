# Gemini Live Dictation

Small, local-first Windows command-line dictation tool for `gemini-3.5-transcribe-live`.
It streams 16 kHz mono microphone audio to Gemini and prints both interim and finalized
transcription. It can also stream a saved audio file at playback speed. Automatic language
detection supports Chinese-English code switching.

## Privacy defaults

- The API key is read from `GEMINI_API_KEY`, optionally via an ignored `.env` file.
- `config/vocabulary.txt`, `transcripts/`, and `recordings/` are ignored by Git.
- No API key, audio recording, transcript, or personal vocabulary is included in source control.

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

Create a private vocabulary file from the example:

```powershell
Copy-Item config\vocabulary.example.txt config\vocabulary.txt
```

Add one term or phrase per line. The local file is intentionally ignored by Git. Gemini accepts
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
gemini-live-dictation --file "C:\Users\ustcy\Documents\test.m4a" --vocabulary config\vocabulary.txt --mode SMART --output transcripts\live-test.txt
```

The file is decoded locally and streamed at its original speed. Only final text is saved;
the audio file is never copied into this repository.

## Next steps

1. Verify microphone selection and transcription quality with a short controlled passage.
2. Tune the private vocabulary list.
3. Add a small GUI after the command-line workflow is stable.
