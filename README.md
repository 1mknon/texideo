# Texideo

**Video editing by text editing — simple as it should be.**

Texideo allows video editing from subtitle's timestamp changes. Each line is turned into a short hash (`md5(text + start_time)`), creating stable anchors that survive reordering, slight text changes, or differences in framerate. The result: frame‑accurate cuts without manual timeline scrubbing.

This is an old‑school proof of concept built with modern tools we often take for granted. It brings consistency to declarative workflows and saves a lot of time when editing speech manually. If you've ever struggled with timeline drags just to remove a sentence, Texideo was made for you.

Quality will vary with speech clarity, the transcription tool you choose, and the precision of your subtitles. If you supply your own subtitles, Whisper can be bypassed entirely.

Accuracy scoring runs independently from transcription, so it may report false positive mismatches.

The full concept isn`t implemented yet, but the current hash‑based system is already remarkably reliable. The goal is to make the tool accessible and break the ice for beginners who want to create fine art.

## Features

- **Transcription** via `faster-whisper` (CPU/GPU)
- **Text‑based editing** – delete, reorder, or modify subtitle lines; the matching video segment is always found through its hash
- **Professional cutting** – output as ProRes, H.264/HEVC, VP9, the original codec, or audio only
- **Persistent process log** – every action (transcription, ffmpeg commands, progress) recorded per job
- **Source media info** – codec, resolution, duration logged automatically
- **Web frontend** (Flask) – drag‑and‑drop upload, live editing, accuracy review, and job management
- **Export** to EDL, FCP XML, and OTIO (DaVinci Resolve, Premiere, etc.) – still under validation
- **Fully configurable** via `config.cfg` – all cutting parameters, timeouts, and profiles
- **Hash protection** – internal anchors allow you to reorder lines freely; 
    a future module may add word‑level control and possibly anonymize content during processing

> **Experimental:** The checkboxes “Keep all before first line” / “Keep all after last line” preserve the first and last subtitle lines with the lines INCLUDED. Adding silent anchors is tricky and it will be adressed in a future new hash module.

## Requirements

- Python 3.10+
- FFmpeg and FFprobe (system packages, e.g. `apt install ffmpeg`)
- Python packages (installed automatically from `requirements.txt`):

```
faster-whisper
opentimelineio
flask
```

The core engine works without Flask – see the “texideo_core” section below.

## Quick start

### CLI

```bash
# Clone the repository
git clone https://github.com/1mknon/texideo.git
cd texideo

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# (Optional) Install the core engine as a standalone package
pip install -e ./texideo_core

# 1. Transcribe a video
python texideo.py transcribe my_video.mp4

# 2. Edit the transcript (opens your configured editor)
python texideo.py edit --job-id <job_id>

# 3. Cut the final video (choose format and codec)
python texideo.py cut --job-id <job_id> --output-format h264

# 4. Export for professional software
python texideo.py export edl --job-id <job_id>
python texideo.py export xml --job-id <job_id>
python texideo.py export otio --job-id <job_id>
```

### Web interface

```bash
python frontend/app.py
```

Open `http://localhost:5000`. Upload a video, edit the transcript, choose output settings, and cut.  
Use the side panel to load existing jobs or re‑edit completed projects.  
The process log can be viewed live and downloaded.

## Configuration

Copy `config.cfg.example` to `config.cfg` and adjust as needed.  
All cutting parameters, codec defaults, and tool paths can be changed without touching the code.  
The config is a JSON file with sections for each part of the pipeline. See the example file for details.

Key settings (also available through the web config editor):

| Setting | Description | Default |
|---------|-------------|--------- |
| `01_transcriber.defaults.model` | Whisper model size | `"base"` |
| `01_transcriber.defaults.device` | `"cpu"` or `"cuda"` | `"cuda"` |
| `05_ffmpeg_defaults.vcodec` | Video codec for cutting | `"libx264"` |
| `05_ffmpeg_defaults.crf` | CRF value (quality) | `18` |
| `06_cutting_params.max_pad` | Breathing room at cut boundaries (seconds) | `0.3` |
| `07_editor` | Text editor command | `"nano"` |

## texideo_core – standalone engine

The cutting engine is now a separate Python package that can be used without the web frontend or CLI.

### Install directly from GitHub

```bash
pip install git+https://github.com/1mknon/texideo.git#subdirectory=texideo_core
```

### Use in your own scripts

```python
from texideo_core import render, edit, config
from texideo_core.anchor import load_anchors

# Example: cut a video using a project saved in the work directory
render.cut(
    video="source.mp4",
    ordered_hashes=["abc123", "def456"],
    output_format="h264"
)
```

The package depends only on `faster-whisper` and `opentimelineio`. FFmpeg must still be installed on the system.

## Project structure

```
texideo/
├── texideo.py                # CLI entry point
├── texideo_core/             # Core engine (pip-installable)
│   ├── setup.py
│   └── texideo_core/
│       ├── anchor.py         # Hash generation and anchor management
│       ├── edit.py           # Text editing and hash matching
│       ├── render.py         # Video cutting and FFmpeg integration
│       ├── config.py         # Configuration loader
│       ├── check.py          # Accuracy review (Whisper comparison)
│       └── export.py         # EDL/XML/OTIO export
├── frontend/                 # Flask web interface
│   ├── app.py
│   ├── templates/
│   └── static/
├── dev/                      # Development tools (tests, validators)
├── output/                   # Working directory (jobs, profiles) – gitignored
├── config.cfg                # User configuration – gitignored
├── config.cfg.example        # Example configuration
├── requirements.txt
└── README.md
```

Development tools (test videos, validator, user simulator) in `dev/` folder and are not part of the public package.

## Why hashes?

Every subtitle line is turned into a unique hash (`md5(text + start_time)`).  
Because the timestamp is in seconds rather than frames, the resulting cut is reproducible and independent of video framerate.  
Even if you slightly edit the text, the system can still locate the right segment – or safely ignore it when no match exists.

## License

Texideo is free software under the **GNU GPLv3**.  
Commercial services (hosting, API, support) are explicitly allowed.
