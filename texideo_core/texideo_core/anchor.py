import json
import os
import hashlib
from . import paths

def compute_hash(text: str, start: float) -> str:
    seed = f"{text}{start:.6f}"
    return hashlib.md5(seed.encode()).hexdigest()[:8]

def generate_anchors(segments: list) -> list:
    anchors = []
    for seg in segments:
        h = compute_hash(seg['text'], seg['start'])
        anchors.append({
            'hash': h,
            'text': seg['text'],
            'start': float(seg['start']),
            'end': float(seg['end'])
        })
    return anchors

def save_anchors(anchors: list, work_dir=None):
    if work_dir is None:
        work_dir = paths.get_work_dir()
    path = os.path.join(work_dir, ".anchors", "map.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(anchors, f, indent=2, ensure_ascii=False)

def load_anchors(work_dir=None) -> list:
    if work_dir is None:
        work_dir = paths.get_work_dir()

    path = os.path.join(work_dir, ".anchors", "map.json")
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return [_normalize(item) for item in data]

    # Fallbacks
    path = os.path.join(work_dir, "dna", "map.json")
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return [_normalize(item) for item in data]

    path = os.path.join(work_dir, "dna", "mapa_ancoras.json")
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return [_normalize(item) for item in data]

    return []

def _normalize(item: dict) -> dict:
    h = item.get('hash')
    if not h:
        txt = item.get('text') or item.get('palavra') or ''
        start = float(item.get('start', 0))
        h = compute_hash(txt, start)
    return {
        'hash': h,
        'text': item.get('text') or item.get('palavra') or '',
        'start': float(item['start']),
        'end': float(item['end'])
    }