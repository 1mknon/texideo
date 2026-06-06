import os
import json
import opentimelineio as otio
from opentimelineio import schema
from . import paths
from .config import load as load_config

def _load_anchors(work_dir):
    map_path = os.path.join(work_dir, ".anchors", "map.json")
    legacy_map = os.path.join(work_dir, "dna", "map.json")
    legacy_mapa = os.path.join(work_dir, "dna", "mapa_ancoras.json")

    data = None
    if os.path.exists(map_path):
        with open(map_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    elif os.path.exists(legacy_map):
        with open(legacy_map, 'r', encoding='utf-8') as f:
            data = json.load(f)
    elif os.path.exists(legacy_mapa):
        with open(legacy_mapa, 'r', encoding='utf-8') as f:
            data = json.load(f)
    else:
        raise FileNotFoundError(f"No anchor file in {work_dir}")

    anchors = {}
    for item in data:
        h = item.get('hash')
        if not h:
            continue
        text = item.get('text', item.get('palavra', ''))
        start = float(item['start'])
        end = float(item['end'])
        anchors[h] = {'text': text, 'start': start, 'end': end}
    return anchors

def _build_timeline(work_dir, ordered_hashes, reel_name=None):
    anchors = _load_anchors(work_dir)
    temp_dir = os.path.join(work_dir, "temp")
    video_path = None
    if os.path.isdir(temp_dir):
        for fname in os.listdir(temp_dir):
            if fname.startswith("source_video."):
                video_path = os.path.join(temp_dir, fname)
                break
    if video_path is None or not os.path.exists(video_path):
        raise FileNotFoundError("Source video not found in temp directory")

    if reel_name is None:
        reel_name = "VIDEO"
    reel_name = reel_name[:8]

    # ── Framerate from config (default 24) ──
    cfg = load_config()
    fps = cfg.get("export_fps", 24)

    timeline = otio.schema.Timeline()
    track = schema.Track(name="Video", kind="Video")

    for h in ordered_hashes:
        a = anchors.get(h)
        if not a:
            continue
        duration = a['end'] - a['start']
        if duration <= 0:
            continue

        start_time = otio.opentime.RationalTime(a['start'], fps)
        dur = otio.opentime.RationalTime(duration, fps)

        media = schema.ExternalReference(
            target_url=f"file://{os.path.abspath(video_path)}",
            available_range=otio.opentime.TimeRange(start_time=start_time, duration=dur)
        )
        media.name = reel_name

        clip = schema.Clip(
            name=a['text'][:50],
            media_reference=media,
            source_range=otio.opentime.TimeRange(start_time=start_time, duration=dur)
        )
        track.append(clip)

    if not track:
        raise ValueError("No valid clips to export")
    timeline.tracks.append(track)
    return timeline

def export(work_dir, fmt, output_path, ordered_hashes, reel_name=None):
    if work_dir is None:
        work_dir = paths.get_work_dir()
    fmt = fmt.lower()
    if fmt == 'edl':
        timeline = _build_timeline(work_dir, ordered_hashes, reel_name)
        otio.adapters.write_to_file(timeline, output_path, adapter_name='cmx_3600')
    elif fmt == 'xml':
        timeline = _build_timeline(work_dir, ordered_hashes, reel_name)
        otio.adapters.write_to_file(timeline, output_path, adapter_name='fcp_xml')
    elif fmt == 'otio':
        timeline = _build_timeline(work_dir, ordered_hashes, reel_name)
        otio.adapters.write_to_file(timeline, output_path, adapter_name='otio_json')
    else:
        raise ValueError(f"Unsupported format: {fmt}. Use 'edl', 'xml', or 'otio'.")
    print(f"Exported {fmt.upper()} to {output_path}")