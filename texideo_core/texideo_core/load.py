import json
import os
import subprocess
import tempfile
from . import paths
from .anchor import compute_hash, generate_anchors, save_anchors, load_anchors, _normalize

def load() -> list:
    return load_anchors()

def save(anchors: list) -> None:
    save_anchors(anchors)

def from_srt(srt_path: str) -> list:
    import re
    anchors_raw = []
    with open(srt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    pattern = re.compile(
        r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\n$|$)',
        re.DOTALL
    )
    for m in pattern.findall(content):
        start_str = m[1].replace(',', '.')
        end_str = m[2].replace(',', '.')
        text = m[3].replace('\n', ' ')
        start = sum(x * float(t) for x, t in zip([3600, 60, 1], start_str.split(':')))
        end = sum(x * float(t) for x, t in zip([3600, 60, 1], end_str.split(':')))
        anchors_raw.append({'text': text, 'start': start, 'end': end})
    return generate_anchors(anchors_raw)

def from_any_subtitle(sub_path: str) -> list:
    ext = os.path.splitext(sub_path)[1].lower()
    if ext == '.srt':
        return from_srt(sub_path)

    # Convert to temporary SRT
    with tempfile.NamedTemporaryFile(suffix='.srt', delete=False) as tmp:
        tmp_srt = tmp.name
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", sub_path, "-f", "srt", tmp_srt
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        anchors = from_srt(tmp_srt)
    finally:
        if os.path.exists(tmp_srt):
            os.remove(tmp_srt)
    return anchors