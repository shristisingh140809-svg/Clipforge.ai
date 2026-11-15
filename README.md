
# ClipForge AI — starter project

A local Flask app that:
- accepts a long video upload
- transcribes speech using faster-whisper
- selects candidate 35-second sections using a simple heuristic
- creates vertical 1080x1920 MP4 clips with word-synced ASS subtitles
- offers four subtitle presets

## Requirements
- Python 3.10 or newer
- FFmpeg and FFprobe installed and available in PATH
- Enough free disk space for uploaded and rendered videos
- Internet connection for the first Whisper model download

## Install

### Windows
1. Install Python 3.10+.
2. Install FFmpeg and add its `bin` folder to PATH.
3. Open this project folder in Command Prompt.
4. Run:

```bash
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000 in your browser.

## Notes
- First run downloads the Whisper `small` model. This can take time.
- CPU transcription can be slow. A GPU can improve performance after configuring faster-whisper.
- Clip selection is a basic heuristic, not a guarantee of viral performance.
- This is a local development prototype, not a secure public production service.
- Before deploying publicly, add authentication, per-user storage, background job processing, upload validation, quotas, cleanup, rate limiting, HTTPS, and stronger secret management.
- Upload only videos you own or have permission to process.
