#!/usr/bin/env python3
"""
Texideo - video editor based on subtitles (hash‑based cutting)

Commands:
  texideo transcribe <video>        Transcribe video and create a job
  texideo edit --job-id ID          Open project.txt for editing (no hashes exposed)
  texideo cut --job-id ID [--profile NAME] [--output-format prores|h264|original|audio_mp3|audio_wav] [--container mov|mp4]   Cut video
  texideo export edl|xml|otio [--output FILE] [--reel-name NAME] --job-id ID   Export timeline
  texideo jobs list                 List all jobs in work directory
  texideo clean [--days N]          Remove jobs older than N days (default 7, --now for 0)
  texideo doctor                    Check dependencies and project state
  texideo --work-dir PATH           Set work directory
  texideo --dry-run                 Simulate without executing
  texideo --help                    Show this help
"""


import sys, os, shutil, subprocess, time, json, uuid

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from texideo_core import paths, edit, render, load, check, export
from texideo_core.anchor import generate_anchors, save_anchors, load_anchors
from texideo_core.config import load as load_config

CFG = load_config()
DRY_RUN = False

def log(msg):
    prefix = "[DRY-RUN] " if DRY_RUN else ""
    print(f"{prefix}{msg}")

def _require_job():
    """Extract --job-id from sys.argv, set work_dir to that job, return its path."""
    if "--job-id" not in sys.argv:
        print("Error: --job-id is required for this command")
        sys.exit(1)
    idx = sys.argv.index("--job-id")
    if idx + 1 >= len(sys.argv):
        print("Error: --job-id requires an argument")
        sys.exit(1)
    job_id = sys.argv[idx + 1]
    job_dir = os.path.join(paths.get_work_dir(), "jobs", job_id)
    if not os.path.exists(job_dir):
        print(f"Job {job_id} not found")
        sys.exit(1)
    paths.set_work_dir(job_dir)
    return job_dir

def set_work_dir(args):
    work_dir = None
    if "--work-dir" in args:
        idx = args.index("--work-dir")
        if idx+1 < len(args):
            work_dir = args[idx+1]
            args = args[:idx] + args[idx+2:]
    if not work_dir:
        work_dir = os.environ.get('TEXIDEO_WORK', os.path.join(BASE, 'output'))
    paths.set_work_dir(work_dir)
    for d in ["jobs"]:
        os.makedirs(os.path.join(work_dir, d), exist_ok=True)
    return args

def doctor():
    print("Texideo Doctor\n==============")
    deps = [("ffmpeg", "ffmpeg"), ("ffprobe", "ffprobe"), ("yt-dlp", "yt-dlp")]
    for name, cmd in deps:
        ok = shutil.which(cmd) is not None
        print(f"  [{'OK' if ok else 'MISS'}] {name}")
    try:
        import faster_whisper
        print("  [OK] faster-whisper")
    except:
        print("  [MISS] faster-whisper (install: pip install faster-whisper)")
    try:
        import opentimelineio as otio
        print("  [OK] opentimelineio")
    except:
        print("  [MISS] opentimelineio (install: pip install opentimelineio)")
    print("Done.")

def transcribe(video):
    if not os.path.exists(video):
        print("Error: video not found")
        return 1
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("Error: faster-whisper not installed")
        return 1
    if DRY_RUN:
        log(f"Would transcribe {video}")
        return 0

    job_id = uuid.uuid4().hex[:12]
    job_dir = os.path.join(paths.get_work_dir(), "jobs", job_id)
    for sub in [".anchors", "temp", "out"]:
        os.makedirs(os.path.join(job_dir, sub), exist_ok=True)
    paths.set_work_dir(job_dir)

    src_ext = os.path.splitext(video)[1]
    dest_name = f"source_video{src_ext}"
    dest = os.path.join(job_dir, "temp", dest_name)
    shutil.copy2(video, dest)

    print(f"Transcribing {video}...")
    model = WhisperModel(CFG.get("whisper_model", "base"), device="cpu", compute_type="int8")
    segments, _ = model.transcribe(dest, word_timestamps=True)
    seg_list = [{'text': seg.text.strip(), 'start': seg.start, 'end': seg.end} for seg in segments]

    anchors = generate_anchors(seg_list)
    save_anchors(anchors)

    print(f"✅ Job {job_id} created. Anchors saved: {len(anchors)} segments")
    print("👉 Next: edit your project")
    print(f"   texideo edit --job-id {job_id}")
    return 0

def edit_cmd():
    job_dir = _require_job()
    job_id = os.path.basename(job_dir)
    text_path = edit.make_text_project()
    if not text_path:
        print("No anchors found. Run 'texideo transcribe' first.")
        return 1
    editor = os.environ.get('EDITOR', CFG.get("editor", "nano"))
    print(f"Opening {text_path} with {editor}...")
    subprocess.run([editor, text_path], check=False)
    with open(text_path, 'r', encoding='utf-8') as f:
        edited_text = f.read()
    ordered_hashes = edit.apply_edit(paths.get_work_dir(), edited_text)
    if not ordered_hashes:
        print("Warning: no matching lines found after editing.")
        return 1
    order_path = paths.project_order()
    with open(order_path, 'w') as f:
        json.dump(ordered_hashes, f)
    print(f"✅ Edit applied. {len(ordered_hashes)} segments will be used.")
    print("👉 Next: cut your video")
    return 0

def cut_cmd(video):
    job_dir = _require_job()
    job_id = os.path.basename(job_dir)

    output_format = CFG.get("output_format", "prores")
    output_container = CFG.get("output_container", "mov")
    if "--output-format" in sys.argv:
        idx = sys.argv.index("--output-format")
        if idx + 1 < len(sys.argv):
            output_format = sys.argv[idx + 1].lower()
    if "--container" in sys.argv:
        idx = sys.argv.index("--container")
        if idx + 1 < len(sys.argv):
            output_container = sys.argv[idx + 1].lower()

    # Profile support
    custom_params = None
    if "--profile" in sys.argv:
        idx = sys.argv.index("--profile")
        if idx + 1 < len(sys.argv):
            profile_name = sys.argv[idx + 1]
            profile_path = os.path.join(BASE, "output", "profiles", f"{profile_name}.ffprofile")
            if os.path.exists(profile_path):
                with open(profile_path) as f:
                    custom_params = json.load(f)
                print(f"Loaded profile '{profile_name}'")
            else:
                print(f"Profile '{profile_name}' not found.")
                return 1

    if video is None:
        temp_dir = paths.temp()
        for fname in os.listdir(temp_dir):
            if fname.startswith("source_video."):
                video = os.path.join(temp_dir, fname)
                break
        if video is None:
            print("Source video not found in temp directory.")
            return 1
    if not os.path.exists(video):
        print("Video not found")
        return 1
    anchors = load_anchors()
    if not anchors:
        print("No anchors found. Run 'texideo transcribe' first.")
        return 1
    order_path = paths.project_order()
    if not os.path.exists(order_path):
        print("Error: project_order.json not found. Run 'texideo edit' first.")
        return 1
    with open(order_path) as f:
        ordered_hashes = json.load(f)
    if DRY_RUN:
        log(f"Would cut {video} with {len(ordered_hashes)} segments")
        return 0

    render.cut(video=video, ordered_hashes=ordered_hashes,
               output_format=output_format, output_container=output_container,
               custom_params=custom_params)

    print(f"✅ Final video saved in job {job_id}")
    print("👉 Export options:")
    print(f"   texideo export edl --job-id {job_id}")
    print(f"   texideo export xml --job-id {job_id}")
    print(f"   texideo export otio --job-id {job_id}")
    print(f"💡 To review/edit again:")
    print(f"   texideo edit --job-id {job_id}")
    return 0

def jobs_list():
    work = paths.get_work_dir()
    jobs_dir = os.path.join(work, "jobs")
    if not os.path.exists(jobs_dir):
        print("No jobs directory found.")
        return
    print(f"{'JOB ID':<14}  {'DATE':<17}  STATUS")
    print("-" * 50)
    for job_id in sorted(os.listdir(jobs_dir)):
        job_path = os.path.join(jobs_dir, job_id)
        if not os.path.isdir(job_path):
            continue
        mtime = os.path.getmtime(job_path)
        date = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
        final_mp4 = os.path.join(job_path, "out", "final.mp4")
        status = "done" if os.path.exists(final_mp4) else "incomplete"
        print(f"{job_id:<14}  {date}  {status}")

def clean(days=7):
    work = paths.get_work_dir()
    jobs_dir = os.path.join(work, "jobs")
    if not os.path.exists(jobs_dir):
        return
    now = time.time()
    removed = 0
    for job_id in os.listdir(jobs_dir):
        job_path = os.path.join(jobs_dir, job_id)
        if os.path.isdir(job_path) and (now - os.path.getmtime(job_path)) > days * 86400:
            if DRY_RUN:
                log(f"Would remove {job_id}")
            else:
                shutil.rmtree(job_path)
                print(f"Removed {job_id}")
                removed += 1
    day_label = "0 days" if days == 0 else f"older than {days} days"
    if not DRY_RUN:
        print(f"Removed {removed} old jobs ({day_label})")

def main():
    global DRY_RUN
    args = sys.argv[1:]
    if "--dry-run" in args:
        DRY_RUN = True
        args.remove("--dry-run")
    args = set_work_dir(args)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = args[0]
    if cmd == "doctor":
        doctor()
    elif cmd == "transcribe":
        if len(args) < 2:
            print("Usage: texideo transcribe <video>")
            return 1
        transcribe(args[1])
    elif cmd == "edit":
        edit_cmd()
    elif cmd == "cut":
        video = None
        if "--video" in args:
            idx = args.index("--video")
            if idx+1 < len(args):
                video = args[idx+1]
        cut_cmd(video)
    elif cmd == "jobs":
        if len(args) > 1 and args[1] == "list":
            jobs_list()
        else:
            print("Usage: texideo jobs list")
            return 1
    elif cmd == "clean":
        days = 7
        if "--now" in args:
            days = 0
        if "--days" in args:
            idx = args.index("--days")
            if idx+1 < len(args):
                days = int(args[idx+1])
        clean(days)
    elif cmd == "export":
        _require_job()
        job_dir = paths.get_work_dir()
        if len(args) < 2:
            print("Usage: texideo export edl|xml|otio [--output FILE] [--reel-name NAME] --job-id ID")
            return 1
        fmt = args[1].lower()
        if fmt not in ('edl', 'xml', 'otio'):
            print("Invalid format. Use edl, xml, or otio.")
            return 1
        out = None
        reel_name = None
        i = 2
        while i < len(args):
            if args[i] == "--output" and i+1 < len(args):
                out = args[i+1]; i += 2
            elif args[i] == "--reel-name" and i+1 < len(args):
                reel_name = args[i+1]; i += 2
            elif args[i] == "--job-id" and i+1 < len(args):
                i += 2
            else:
                i += 1
        if out is None:
            out = os.path.join(job_dir, "out", f"timeline.{fmt}")
        elif not os.path.isabs(out):
            out = os.path.join(job_dir, "out", out)
        order_path = paths.project_order()
        if not os.path.exists(order_path):
            print("Error: project_order.json not found. Run 'texideo edit' first.")
            return 1
        with open(order_path) as f:
            ordered_hashes = json.load(f)
        try:
            export.export(job_dir, fmt, out, ordered_hashes, reel_name)
        except Exception as e:
            print(f"Export failed: {e}")
            return 1
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())