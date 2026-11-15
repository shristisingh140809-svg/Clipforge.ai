import os
import re
import uuid
import json
import shutil
import subprocess
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-development-key")
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1 GB starter limit

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
SUBTITLE_STYLES = {
    "classic": {"font": "Arial", "size": 58, "primary": "&H00FFFFFF", "outline": "&H00000000"},
    "yellow": {"font": "Arial", "size": 62, "primary": "&H0000FFFF", "outline": "&H00000000"},
    "clean": {"font": "Arial", "size": 48, "primary": "&H00FFFFFF", "outline": "&H80000000"},
    "bold": {"font": "Arial", "size": 68, "primary": "&H0000FFFF", "outline": "&H00000000"},
}

def run_command(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2500:] or "A video-processing command failed.")
    return result

def check_tools():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and FFprobe are required. Install FFmpeg and add it to PATH.")

def get_duration(video_path):
    result = run_command([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)
    ])
    return float(result.stdout.strip())

def transcribe(video_path):
    # faster-whisper runs locally. First run downloads the selected model.
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(video_path), word_timestamps=True, vad_filter=True
    )
    data = []
    for seg in segments:
        words = []
        if seg.words:
            for word in seg.words:
                words.append({
                    "start": float(word.start),
                    "end": float(word.end),
                    "text": word.word.strip()
                })
        data.append({
            "start": float(seg.start),
            "end": float(seg.end),
            "text": seg.text.strip(),
            "words": words
        })
    return data, getattr(info, "language", "unknown")

def choose_clips(segments, duration, clip_length=35, max_clips=5):
    """Simple heuristic clip finder; this is not a trained virality predictor."""
    if duration <= 0:
        return []
    candidates = []
    step = max(12, clip_length // 2)
    start = 0.0
    while start < duration:
        end = min(duration, start + clip_length)
        if end - start >= 12:
            selected = [s for s in segments if s["end"] > start and s["start"] < end]
            text = " ".join(s["text"] for s in selected)
            word_count = len(re.findall(r"\b[\w']+\b", text))
            if word_count >= 8:
                lower = text.lower()
                hooks = ["why", "secret", "mistake", "never", "best", "worst",
                         "shocking", "important", "because", "actually", "insane",
                         "amazing", "problem", "truth", "finally", "how"]
                hook_score = sum(lower.count(w) for w in hooks)
                punctuation_score = text.count("?") + text.count("!")
                density = word_count / max(end - start, 1)
                score = density + hook_score * 0.8 + punctuation_score * 0.5
                candidates.append({"start": start, "end": end, "score": score, "text": text})
        start += step

    # Keep the best windows while avoiding clips that overlap heavily.
    candidates.sort(key=lambda x: x["score"], reverse=True)
    chosen = []
    for item in candidates:
        if all(item["end"] <= c["start"] + 3 or item["start"] >= c["end"] - 3 for c in chosen):
            chosen.append(item)
        if len(chosen) >= max_clips:
            break
    chosen.sort(key=lambda x: x["start"])
    return chosen

def ass_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"

def ass_escape(text):
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", " ")

def create_ass(segments, clip_start, clip_end, style_name, output_path):
    style = SUBTITLE_STYLES.get(style_name, SUBTITLE_STYLES["classic"])
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style['font']},{style['size']},{style['primary']},&H000000FF,{style['outline']},&H80000000,-1,0,1,4,2,2,70,70,230,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for seg in segments:
        words = [w for w in seg.get("words", []) if w["end"] > clip_start and w["start"] < clip_end]
        # Group words into short readable caption lines.
        for i in range(0, len(words), 5):
            group = words[i:i+5]
            if not group:
                continue
            start = max(group[0]["start"], clip_start) - clip_start
            end = min(group[-1]["end"], clip_end) - clip_start
            if end <= start:
                continue
            karaoke = []
            for w in group:
                duration_cs = max(1, int((w["end"] - w["start"]) * 100))
                karaoke.append(r"{\k" + str(duration_cs) + "}" + ass_escape(w["text"]))
            text = " ".join(karaoke)
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}")
    Path(output_path).write_text(header + "\n".join(lines) + "\n", encoding="utf-8")

def make_clip(source, clip, index, style_name, job_id):
    output_name = f"{job_id}_clip_{index}.mp4"
    output_path = OUTPUT_DIR / output_name
    ass_path = OUTPUT_DIR / f"{job_id}_clip_{index}.ass"
    # Use original transcript data saved on the clip object for captions.
    create_ass(clip["segments"], clip["start"], clip["end"], style_name, ass_path)
    ass_filter_path = str(ass_path).replace("\\", "/").replace(":", r"\:")
    vf = f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,subtitles='{ass_filter_path}'"
    run_command([
        "ffmpeg", "-y", "-ss", str(clip["start"]), "-i", str(source),
        "-t", str(clip["end"] - clip["start"]), "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        str(output_path)
    ])
    return output_name

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        uploaded = request.files.get("video")
        style = request.form.get("style", "classic")
        count = max(1, min(8, int(request.form.get("count", "3"))))
        if not uploaded or not uploaded.filename:
            flash("Choose a video first.")
            return redirect(url_for("index"))
        extension = Path(secure_filename(uploaded.filename)).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            flash("Please upload MP4, MOV, MKV, WEBM or AVI.")
            return redirect(url_for("index"))

        job_id = uuid.uuid4().hex[:10]
        source_path = UPLOAD_DIR / f"{job_id}{extension}"
        uploaded.save(source_path)
        try:
            check_tools()
            duration = get_duration(source_path)
            segments, language = transcribe(source_path)
            candidates = choose_clips(segments, duration, max_clips=count)
            if not candidates:
                raise RuntimeError("No suitable speech sections were found. Try a video with clear spoken audio.")
            generated = []
            for i, clip in enumerate(candidates, start=1):
                clip["segments"] = segments
                filename = make_clip(source_path, clip, i, style, job_id)
                generated.append({
                    "filename": filename,
                    "start": round(clip["start"], 1),
                    "end": round(clip["end"], 1),
                    "preview": (clip["text"][:180] + "...") if len(clip["text"]) > 180 else clip["text"]
                })
            return render_template("results.html", clips=generated, language=language)
        except Exception as exc:
            flash(f"Could not process this video: {exc}")
            return redirect(url_for("index"))
        finally:
            # Keep generated clips; remove the uploaded source to save disk space.
            try:
                source_path.unlink(missing_ok=True)
            except Exception:
                pass
    return render_template("index.html")

@app.route("/download/<path:filename>")
def download(filename):
    # Only allow files in OUTPUT_DIR.
    safe_name = Path(filename).name
    return send_from_directory(OUTPUT_DIR, safe_name, as_attachment=True)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)