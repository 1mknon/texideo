import subprocess
import os
import json
import shutil
from . import paths
from .anchor import load_anchors
from .config import load as load_config, get_ffmpeg


# Helper: fill {placeholders} in a list of strings
def _fill_template(template, params):
    """Replace {key} with *params[key]* in each string of *template*.
    
    Empty strings are discarded (useful for unused optional arguments).
    """
    result = []
    for part in template:
        for key, value in params.items():
            part = part.replace("{" + key + "}", str(value))
        if part.strip():
            result.append(part)
    return result



# Path sanitizer (for logging)
def _sanitize_path(absolute_path, work_dir):
    home = os.path.expanduser('~')
    if absolute_path.startswith(home):
        return '~' + absolute_path[len(home):]
    if absolute_path.startswith(work_dir):
        return os.path.relpath(absolute_path, work_dir)
    return absolute_path



# SRT generation (unchanged)
def _generate_srt(segments, output_srt):
    def format_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    accumulated = 0.0
    with open(output_srt, 'w', encoding='utf-8') as f:
        for idx, seg in enumerate(segments, 1):
            duration = seg['end'] - seg['start']
            f.write(f"{idx}\n{format_time(accumulated)} --> {format_time(accumulated + duration)}\n{seg['text']}\n\n")
            accumulated += duration



# Ordered-hash helpers (unchanged)
def _original_indices(ordered_hashes, anchors):
    hash_to_idx = {a['hash']: i for i, a in enumerate(anchors)}
    return [hash_to_idx[h] for h in ordered_hashes if h in hash_to_idx]


def _group_consecutive(indices):
    if not indices:
        return []
    groups = []
    current = [indices[0]]
    for i in range(1, len(indices)):
        if indices[i] == indices[i-1] + 1:
            current.append(indices[i])
        else:
            groups.append(current)
            current = [indices[i]]
    groups.append(current)
    return groups


def _adjust_boundaries(groups, anchors, video_duration=None, max_pad=0.3, internal_pad=0.1):
    if not groups:
        return groups

    total_anchors = len(anchors)
    for group in groups:
        # internal padding
        if group[0] > 0:
            prev_end = anchors[group[0] - 1]['end']
            silence = anchors[group[0]]['start'] - prev_end
            if silence > 0:
                pad = min(internal_pad, silence / 2)
                anchors[group[0]]['start'] = round(anchors[group[0]]['start'] - pad, 6)
        if group[-1] < total_anchors - 1:
            next_start = anchors[group[-1] + 1]['start']
            silence = next_start - anchors[group[-1]]['end']
            if silence > 0:
                pad = min(internal_pad, silence / 2)
                anchors[group[-1]]['end'] = round(anchors[group[-1]]['end'] + pad, 6)

    # max padding for first and last group
    first_group, last_group = groups[0], groups[-1]
    if first_group[0] > 0:
        prev_end = anchors[first_group[0] - 1]['end']
        silence = anchors[first_group[0]]['start'] - prev_end
        if silence > 0:
            pad = min(max_pad, silence / 2)
            anchors[first_group[0]]['start'] = round(anchors[first_group[0]]['start'] - pad, 6)

    if last_group[-1] < total_anchors - 1:
        next_start = anchors[last_group[-1] + 1]['start']
        silence = next_start - anchors[last_group[-1]]['end']
        if silence > 0:
            pad = min(max_pad, silence / 2)
            anchors[last_group[-1]]['end'] = round(anchors[last_group[-1]]['end'] + pad, 6)
    elif last_group[-1] == total_anchors - 1 and video_duration is not None:
        anchors[last_group[-1]]['end'] = video_duration

    return groups



# Build the FFmpeg command for a single cut (now template‑driven)
def _build_encode_cmd(ffmpeg_exe, source_path, start, duration, output_path,
                      output_format, custom_params=None):
    """Build the FFmpeg command for a single cut block.

    Uses the template from ``03_cut_command`` in the configuration,
    but handles ``{extra_args}`` specially: it removes the placeholder
    from the template and appends the extra arguments as separate list
    items afterwards.
    """
    cfg = load_config()
    cp = custom_params or {}
    ff_defaults = cfg.get("05_ffmpeg_defaults", {})

    # Load the cutting template (always a list of strings)
    cut_template = cfg.get("03_cut_command", [
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
        "{output_file}"       # {extra_args} removed – handled separately
    ])

    # Parameter gathering (same as before)
    vcodec = cp.get('vcodec') or ff_defaults.get('vcodec', 'libx264')
    preset = cp.get('preset') or ff_defaults.get('preset', 'fast')
    crf = cp.get('crf') or ff_defaults.get('crf', 18)
    gop = cp.get('gop') if cp.get('gop') is not None else ff_defaults.get('gop', 1)
    extra = cp.get('extra') or ff_defaults.get('extra_args', [])
    audio_bitrate = cp.get('audio_bitrate') or ff_defaults.get('audio_bitrate', '256k')

    # Codec‑specific adjustments
    if vcodec == 'libvpx-vp9':
        acodec = 'libopus'
        # Ensure -deadline good is present (but do NOT add it as a single string)
        if '-deadline' not in extra and 'good' not in extra:
            extra = extra + ['-deadline', 'good']
    else:
        acodec = cp.get('acodec') or ff_defaults.get('acodec', 'aac')

    #  Special formats (do not use the template) 
    if output_format == 'original':
        return [ffmpeg_exe, "-y", "-i", source_path,
                "-ss", str(start), "-t", str(duration),
                "-c", "copy", output_path]

    if output_format.startswith('audio_'):
        codec = cp.get('acodec') or ('libmp3lame' if output_format == 'audio_mp3' else 'pcm_s16le')
        cmd = [ffmpeg_exe, "-y", "-i", source_path,
               "-ss", str(start), "-t", str(duration),
               "-vn", "-acodec", codec]
        if cp.get('vbr') and codec == 'libmp3lame':
            cmd += ["-q:a", str(cp.get('audio_quality', 2))]
        elif not cp.get('vbr'):
            cmd += ["-b:a", cp.get('audio_bitrate') or
                    ('320k' if output_format == 'audio_mp3' else '256k')]
        cmd.append(output_path)
        return cmd

    if output_format == 'prores':
        profile_str = cp.get('prores_profile') or cfg.get("prores_profile", "lt")
        profile_map = {"proxy": 0, "lt": 1, "standard": 2, "hq": 3}
        try:
            profile_num = int(profile_str)
        except ValueError:
            profile_num = profile_map.get(profile_str.lower(), 1)
        return [ffmpeg_exe, "-y", "-i", source_path,
                "-ss", str(start), "-t", str(duration),
                "-c:v", "prores_ks", "-profile:v", str(profile_num),
                "-vsync", "cfr",
                "-c:a", "pcm_s16le", "-f", "mov", output_path]

    #  Standard (h264/VP9/…) using the template 
    params = {
        "input_file": source_path,
        "start_time": str(start),
        "duration": str(duration),
        "vcodec": vcodec,
        "preset": str(preset),
        "crf": str(crf),
        "gop": str(gop),
        "acodec": acodec,
        "audio_bitrate": audio_bitrate,
        "output_file": output_path
    }

    # Remove the placeholder {extra_args} from the template (if present)
    template_clean = [part for part in cut_template if part.strip() != "{extra_args}"]

    # Skip the leading "ffmpeg" if it's the first element (we already have ffmpeg_exe)
    if template_clean and template_clean[0] == "ffmpeg":
        template_args = template_clean[1:]
    else:
        template_args = template_clean

    # Build the command
    cmd = [ffmpeg_exe] + _fill_template(template_args, params)

    # Append any extra arguments (each as a separate item)
    if extra:
        cmd.extend(extra)

    return cmd



# Main cut function
def cut(video=None, ordered_hashes=None, output_format='prores', output_container='mov',
        keep_intro=False, keep_outro=False, progress_callback=None, custom_params=None):
    if video is None:
        temp_dir = paths.temp()
        for fname in os.listdir(temp_dir):
            if fname.startswith("source_video."):
                video = os.path.join(temp_dir, fname)
                break
        if video is None:
            print("Error: source video not found in temp directory")
            return

    work_dir = paths.get_work_dir()
    out_dir = os.path.join(work_dir, "out")
    os.makedirs(out_dir, exist_ok=True)

    if not os.path.exists(video):
        print("Error: video not found")
        return

    cfg = load_config()
    ffmpeg_exe = get_ffmpeg(cfg)

    anchors = load_anchors(work_dir)
    if not anchors:
        print("Error: no anchors found. Run transcription first.")
        return

    if ordered_hashes is None:
        order_path = os.path.join(work_dir, ".anchors", "project_order.json")
        if not os.path.exists(order_path):
            print("Error: project_order.json not found and no hashes provided.")
            return
        with open(order_path) as f:
            ordered_hashes = json.load(f)

    # Convert hashes to segments (used for SRT later)
    hash_to_seg = {a['hash']: a for a in anchors}
    segments = []
    for h in ordered_hashes:
        seg = hash_to_seg.get(h)
        if seg:
            segments.append(seg)
        else:
            print(f"Warning: hash {h} not found, skipping.")

    if not segments:
        print("Error: no valid segments to cut.")
        return

    #  Build cut groups 
    original_hashes = [a['hash'] for a in anchors]
    if ordered_hashes == original_hashes and not keep_intro and not keep_outro:
        # No changes: use the whole video as a single group
        print("No changes detected. Re‑encoding full video with chosen format...")
        groups = [list(range(len(anchors)))]
    else:
        orig_indices = _original_indices(ordered_hashes, anchors)
        groups = _group_consecutive(orig_indices)

    #  Probe video duration (needed for boundary adjustment) 
    video_duration = None
    try:
        probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", video]
        video_duration = float(subprocess.check_output(probe_cmd, text=True).strip())
    except:
        pass

    max_pad = cfg.get("max_pad", 0.3)
    internal_pad = cfg.get("internal_pad", 0.1)
    _adjust_boundaries(groups, anchors, video_duration=video_duration,
                       max_pad=max_pad, internal_pad=internal_pad)

    #  Determine block extension and output container 
    cp = custom_params or {}
    vcodec = cp.get('vcodec') or cfg.get("ffmpeg_defaults", {}).get("vcodec", "libx264")

    ext_map = {
        'original': os.path.splitext(video)[1],
        'audio_mp3': '.mp3',
        'audio_wav': '.wav',
        'prores': '.mov',
        'h264': '.webm' if vcodec == 'libvpx-vp9' else '.mp4'   # fixed: .mp4 for standard H.264
    }
    block_ext = ext_map.get(output_format, '.mp4')
    if output_format.startswith('audio_'):
        block_ext = '.mp3' if output_format == 'audio_mp3' else '.wav'

    temp_dir = paths.temp()
    block_files = []

    # Helper to add a block with progress
    def add_block(start, duration, label, block_index, total_blocks):
        if duration < 0.1:
            print(f"  ⏭️  Skipping {label} (duration {duration:.2f}s < 0.1s)")
            return
        out_seg = os.path.join(temp_dir, f"{label}{block_ext}")
        cmd = _build_encode_cmd(ffmpeg_exe, video, start, duration, out_seg,
                                output_format, custom_params)

        timeout = cfg.get("original_timeout", 60)
        if output_format == 'h264':
            factor = cfg.get("h264_timeout_factor", 1.5)
            timeout = max(cfg.get("min_h264_timeout", 60), duration * factor)
        elif output_format == 'prores':
            factor = cfg.get("prores_timeout_factor", 3.0)
            timeout = max(cfg.get("min_prores_timeout", 120), duration * factor)

        sanitized = [_sanitize_path(p, work_dir) if os.path.isabs(p) else p for p in cmd]
        cmd_str = ' '.join(sanitized)

        print(f"  ➕ {label}: {start:.2f}s → {start+duration:.2f}s ({duration:.2f}s, timeout={timeout:.0f}s)")
        if progress_callback:
            progress_callback(0, f"ffmpeg {cmd_str}")

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            for line in proc.stderr:
                if 'time=' in line:
                    time_str = line.split('time=')[1].split()[0]
                    try:
                        h, m, s = time_str.split(':')
                        current_time = float(h)*3600 + float(m)*60 + float(s)
                        if duration > 0:
                            block_progress = min(100, int(current_time / duration * 100))
                            overall = int((block_index + block_progress/100) / total_blocks * 100)
                            if progress_callback:
                                progress_callback(overall, f"Coding block {block_index+1}/{total_blocks} ({block_progress}%)")
                    except:
                        pass
            proc.wait(timeout=timeout)
            if proc.returncode != 0:
                stderr_output = proc.stderr.read() if proc.stderr else ""
                error_msg = f"FFmpeg error (code {proc.returncode}): {stderr_output[-500:]}"
                if progress_callback:
                    progress_callback(0, error_msg)
                raise subprocess.CalledProcessError(proc.returncode, cmd, output=stderr_output)
        except subprocess.TimeoutExpired:
            proc.kill()
            print(f"❌ FFmpeg timed out after {timeout:.0f}s on block {label}")
            raise

        block_files.append(out_seg)
        if progress_callback:
            overall = int((block_index + 1) / total_blocks * 100)
            progress_callback(overall, f"Coded block {block_index+1}/{total_blocks}")

    # Execute all blocks
    total_blocks = len(groups)
    print(f"=== DEBUG: groups = {groups}")
    for idx, group in enumerate(groups, 1):
        first_seg = anchors[group[0]]
        last_seg = anchors[group[-1]]
        print(f"  Group {idx}: {first_seg['start']:.2f} → {last_seg['end']:.2f}  "
              f"duration={last_seg['end'] - first_seg['start']:.2f}")
        start_time = first_seg['start']
        end_time = last_seg['end']
        duration = end_time - start_time
        add_block(start_time, duration, f"block_{group[0]:04d}_{group[-1]:04d}", idx-1, total_blocks)

    if not block_files:
        print("Error: no blocks were cut.")
        return

    # Determine final extension
    if output_format == 'h264':
        final_ext = '.webm' if vcodec == 'libvpx-vp9' else '.mp4'
    else:
        final_ext = block_ext
    output_video = os.path.join(out_dir, f"final{final_ext}")

    # Write concat list
    concat_list = os.path.join(temp_dir, "concat_list.txt")
    with open(concat_list, 'w') as f:
        for bf in block_files:
            f.write(f"file '{os.path.abspath(bf)}'\n")

    print(f"🧩 Concatenating {len(block_files)} blocks...")
    if progress_callback:
        progress_callback(95, "Concatenating...")

    # Use template from config
    concat_template = cfg.get("04_concat_command", [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "{concat_list}",
        "-c", "copy", "{output_file}"
    ])
    template_args = concat_template[1:] if concat_template and concat_template[0] == "ffmpeg" else concat_template
    concat_params = {"concat_list": concat_list, "output_file": output_video}
    cmd_concat = [ffmpeg_exe] + _fill_template(template_args, concat_params)
    subprocess.run(cmd_concat, check=True)

    # Clean up temporary blocks
    for bf in block_files:
        os.remove(bf)

    print(f"Generating SRT with {len(segments)} segments...")
    _generate_srt(segments, os.path.join(out_dir, "final.srt"))

    print(f"✅ Final video saved: {output_video}")
    if progress_callback:
        progress_callback(100, "Completed")