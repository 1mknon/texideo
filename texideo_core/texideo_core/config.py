import json
import os
import shutil
import sys

DEFAULT_CONFIG = {
    "00_comment": "Save this file in the Texideo project root (same folder as texideo.py). When GPU fail will fallback to CPU.",
    "01_transcriber": {
        "executable": "faster-whisper",
        "args": ["--model", "{model}", "--language", "{language}", "--device", "{device}",
                 "--compute_type", "{compute_type}", "{input_file}", "--output_format", "json"],
        "defaults": {"model": "base", "language": None, "device": "cuda", "compute_type": "int8"},
        "output_parser": "faster_whisper_json"
    },
    "02_review": {
        "enabled": True,
        "executable": "faster-whisper",
        "args": ["--model", "{model}", "--language", "{language}", "--device", "{device}",
                 "--compute_type", "{compute_type}", "{input_file}", "--output_format", "json"],
        "defaults": {"model": "base", "language": None, "device": "cpu", "compute_type": "int8"},
        "output_parser": "faster_whisper_json"
    },
    "03_cut_command": [
        "ffmpeg", "-y",
        "-i", "{input_file}",
        "-ss", "{start_time}",
        "-t", "{duration}",
        "-c:v", "{vcodec}",
        "-preset", "{preset}",
        "-crf", "{crf}",
        "-g", "{gop}",
        "-c:a", "{acodec}",
        "-b:a", "{audio_bitrate}",
        "{extra_args}",
        "{output_file}"
    ],
    "04_concat_command": [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", "{concat_list}",
        "-c", "copy",
        "{output_file}"
    ],
    "05_ffmpeg_defaults": {
        "vcodec": "libx264",
        "preset": "fast",
        "crf": 18,
        "gop": 1,
        "acodec": "aac",
        "audio_bitrate": "256k",
        "extra_args": []
    },
    "06_cutting_params": {
        "max_pad": 0.3,
        "internal_pad": 0.1
    },
    "07_editor": "nano",
    "whisper_model": "base",
    "whisper_language": None,
    "whisper_device": "cuda",
    "whisper_compute_type": "int8",
    "editor": "nano",
    "max_pad": 0.3,
    "internal_pad": 0.1,
    "ffmpeg_timeout": 3600,
    "work_dir": None,
    "reel_name": "VIDEO",
    "ffmpeg_path": "ffmpeg",
    "ffprobe_path": "ffprobe",
    "output_format": "prores",
    "output_container": "mov",
    "prores_profile": "lt",
    "h264_crf": 18,
    "prores_timeout_factor": 3.0,
    "h264_timeout_factor": 1.5,
    "original_timeout": 60,
    "min_prores_timeout": 120,
    "min_h264_timeout": 60,
    "export_fps": 24
}


def load(path=None):
    if path is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "config.cfg")

    cfg = dict(DEFAULT_CONFIG)  # start with a copy of defaults

    if not os.path.exists(path):
        _add_compat_keys(cfg)
        return cfg

    with open(path, 'r', encoding='utf-8') as f:
        user = json.load(f)

    if "01_transcriber" in user:
        _deep_update(cfg, user)
    else:
        for key in user:
            cfg[key] = user[key]

    _add_compat_keys(cfg)
    return cfg


def _deep_update(original, update):
    for key, value in update.items():
        if isinstance(value, dict) and key in original and isinstance(original[key], dict):
            _deep_update(original[key], value)
        else:
            original[key] = value


def _add_compat_keys(cfg):
    trans = cfg.get("01_transcriber", {}).get("defaults", {})
    review = cfg.get("02_review", {}).get("defaults", {})
    ffmpeg_def = cfg.get("05_ffmpeg_defaults", {})
    cutting = cfg.get("06_cutting_params", {})

    cfg["whisper_model"] = trans.get("model", "base")
    cfg["whisper_language"] = trans.get("language", None)
    cfg["whisper_device"] = trans.get("device", "cpu")
    cfg["whisper_compute_type"] = trans.get("compute_type", "int8")
    cfg["editor"] = cfg.get("07_editor", "nano")
    cfg["max_pad"] = cutting.get("max_pad", 0.3)
    cfg["internal_pad"] = cutting.get("internal_pad", 0.1)

    for k, v in ffmpeg_def.items():
        cfg.setdefault(k, v)


def get_ffmpeg(cfg):
    path = cfg.get("ffmpeg_path", "ffmpeg")
    exe = shutil.which(path)
    if exe is None:
        print(f"⚠️  Configured ffmpeg '{path}' not found. Falling back to system 'ffmpeg'.", file=sys.stderr)
        exe = shutil.which("ffmpeg")
    if exe is None:
        raise RuntimeError("ffmpeg not found in PATH.")
    return exe


def get_ffprobe(cfg):
    path = cfg.get("ffprobe_path", "ffprobe")
    exe = shutil.which(path)
    if exe is None:
        print(f"⚠️  Configured ffprobe '{path}' not found. Falling back to system 'ffprobe'.", file=sys.stderr)
        exe = shutil.which("ffprobe")
    if exe is None:
        raise RuntimeError("ffprobe not found in PATH.")
    return exe