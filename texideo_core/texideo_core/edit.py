import json
from . import paths
from .anchor import load_anchors

def normalize_edit(text):
    return [line.strip() for line in text.splitlines() if line.strip()]

def make_text_project(work_dir=None):
    if work_dir is None:
        work_dir = paths.get_work_dir()
    anchors = load_anchors(work_dir)
    if not anchors:
        return None
    lines = [a['text'] for a in anchors]
    text_path = paths.project_text()
    with open(text_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return text_path

def apply_edit(work_dir, edited_text):
    anchors = load_anchors(work_dir)
    if not anchors:
        return []
    lines = normalize_edit(edited_text)
    text_to_hashes = {}
    for a in anchors:
        text_to_hashes.setdefault(a['text'], []).append(a['hash'])
    used = {}
    ordered_hashes = []
    matched = 0
    for line in lines:
        if line in text_to_hashes:
            idx = used.get(line, 0)
            if idx < len(text_to_hashes[line]):
                ordered_hashes.append(text_to_hashes[line][idx])
                used[line] = idx + 1
                matched += 1
    # Optional debug: print(f"Lines: {len(lines)}, Matched: {matched}")
    return ordered_hashes