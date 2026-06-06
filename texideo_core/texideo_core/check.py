import subprocess
import os
import re
import tempfile
import logging
from . import paths
from texideo_core.config import load as load_config, get_ffmpeg

cfg = load_config()
ffmpeg_exe = get_ffmpeg(cfg)

log = logging.getLogger('texideo-check')

def review(work_dir=None, verbose=True):
    if work_dir is None:
        work_dir = paths.get_work_dir()
    out_dir = os.path.join(work_dir, "out")
    final_video = None
    for f in os.listdir(out_dir):
        if f.startswith('final.') and not f.endswith('.srt'):
            final_video = os.path.join(out_dir, f)
            break
    if not final_video or not os.path.exists(final_video):
        return False, {"error": f"final video not found in {out_dir}"}

    expected_text = None
    text_path = paths.project_text()
    if os.path.exists(text_path):
        with open(text_path, 'r', encoding='utf-8') as f:
            expected_text = f.read().replace('\n', ' ')
    else:
        proj_path = os.path.join(work_dir, "projeto_edicao.txt")
        if os.path.exists(proj_path):
            lines = []
            with open(proj_path, 'r', encoding='utf-8') as f:
                for line in f:
                    clean = re.sub(r'^\[[^\]]+\]\s*', '', line.strip())
                    if clean:
                        lines.append(clean)
            expected_text = " ".join(lines)
        else:
            return False, {"error": "No project text found (project.txt or projeto_edicao.txt missing)"}

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return False, {"error": "faster-whisper not installed"}

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = tmp.name
    try:
        subprocess.run([
            ffmpeg_exe, "-y", "-i", final_video,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "2", audio_path
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        temp_audio = audio_path + ".mka"
        try:
            subprocess.run([
                ffmpeg_exe, "-y", "-i", final_video,
                "-vn", "-acodec", "copy", temp_audio
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run([
                ffmpeg_exe, "-y", "-i", temp_audio,
                "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "2", audio_path
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.unlink(temp_audio)
        except Exception:
            os.unlink(audio_path)
            return False, {"error": "Audio extraction failed"}

    detected_lang = None
    lang_file = os.path.join(work_dir, "detected_lang.txt")
    if os.path.exists(lang_file):
        with open(lang_file, "r") as f:
            detected_lang = f.read().strip()

    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, info = model.transcribe(audio_path, language=detected_lang)
    transcribed_text = " ".join(seg.text for seg in segments).strip()
    os.unlink(audio_path)

    expected_words = set(re.findall(r'\w+', expected_text.lower()))
    transcribed_words = set(re.findall(r'\w+', transcribed_text.lower()))
    common = expected_words & transcribed_words
    recall = (len(common) / len(expected_words) * 100) if expected_words else 100
    precision = (len(common) / len(transcribed_words) * 100) if transcribed_words else 0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0
    ok = recall >= 95

    result = {
        "ok": ok,
        "accuracy": round(recall, 2),
        "precision": round(precision, 2),
        "recall": round(recall, 2),
        "f1": round(f1, 2),
        "matched": len(common),
        "total": len(expected_words),
        "missing": list(expected_words - transcribed_words)[:10],
        "extra": list(transcribed_words - expected_words)[:10]
    }
    if verbose:
        print(f"Review: {recall:.1f}% of expected words found. F1: {f1:.1f}%")
    return ok, result