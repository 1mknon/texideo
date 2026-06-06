import sys
import os
import uuid
import shutil
import json
import threading
import logging
import tempfile
import subprocess
import time

from flask import Flask, render_template, request, send_from_directory, jsonify, Response

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from texideo_core import paths, render, check
from texideo_core.anchor import generate_anchors, save_anchors, load_anchors
from texideo_core.edit import normalize_edit
from texideo_core.config import load as load_config, get_ffmpeg, DEFAULT_CONFIG

cfg = load_config()
FFMPEG = get_ffmpeg(cfg)

RESULTS_ROOT = os.path.join(BASE_DIR, "output", "jobs")
PROFILES_DIR = os.path.join(BASE_DIR, "output", "profiles")
TEMPLATE_DIR = os.path.join(BASE_DIR, "frontend", "templates")
os.makedirs(RESULTS_ROOT, exist_ok=True)
os.makedirs(PROFILES_DIR, exist_ok=True)
os.makedirs(TEMPLATE_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger('texideo-frontend')

app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024

jobs = {}
jobs_lock = threading.Lock()


# Helper functions
def _relative(path):
    try:
        return os.path.relpath(path, BASE_DIR)
    except ValueError:
        return path

def _write_log(job_id, message):
    log_file = os.path.join(RESULTS_ROOT, job_id, "process.log")
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")

def _load_existing_jobs():
    if not os.path.isdir(RESULTS_ROOT):
        return
    for job_id in os.listdir(RESULTS_ROOT):
        job_dir = os.path.join(RESULTS_ROOT, job_id)
        if not os.path.isdir(job_dir):
            continue
        video_name = "unknown"
        video_name_file = os.path.join(job_dir, "video_name.txt")
        if os.path.exists(video_name_file):
            with open(video_name_file, "r") as f:
                video_name = f.read().strip()
        out_dir = os.path.join(job_dir, "out")
        status = "incomplete"
        narrative = None
        original_narrative = None

        anchors_path = os.path.join(job_dir, ".anchors", "map.json")
        if os.path.exists(anchors_path):
            try:
                with open(anchors_path, "r") as f:
                    anchors_data = json.load(f)
                texts = [a.get('text', a.get('palavra', '')) for a in anchors_data if a.get('text') or a.get('palavra')]
                original_narrative = "\n".join(texts) if texts else None
            except:
                original_narrative = None

        if os.path.isdir(out_dir) and any(f.startswith('final.') for f in os.listdir(out_dir)):
            status = "review"
        elif os.path.exists(os.path.join(job_dir, ".anchors", "map.json")):
            status = "ready_for_edit"
            proj_txt = os.path.join(job_dir, "project.txt")
            if os.path.exists(proj_txt):
                with open(proj_txt, "r") as f:
                    narrative = f.read()
            else:
                anchors = load_anchors(job_dir)
                if anchors:
                    narrative = "\n".join(a['text'] for a in anchors)

        jobs[job_id] = {
            'status': status,
            'step': 2 if status == 'review' else 1,
            'progress': 1.0,
            'message': '',
            'work_dir': job_dir,
            'narrative': narrative,
            'original_narrative': original_narrative,
            'video_name': video_name
        }

with app.app_context():
    _load_existing_jobs()

def _make_job_dir(job_id):
    job_dir = os.path.join(RESULTS_ROOT, job_id)
    for sub in ['.anchors', 'temp', 'out']:
        os.makedirs(os.path.join(job_dir, sub), exist_ok=True)
    return job_dir

def _apply_edit_from_map(job_dir, edited_text):
    anchors = load_anchors(job_dir)
    if not anchors:
        return []
    lines = normalize_edit(edited_text)
    text_to_hashes = {}
    for a in anchors:
        t = a['text']
        if t not in text_to_hashes:
            text_to_hashes[t] = []
        text_to_hashes[t].append(a['hash'])
    used_count = {}
    ordered_hashes = []
    for line in lines:
        if line in text_to_hashes:
            idx = used_count.get(line, 0)
            if idx < len(text_to_hashes[line]):
                ordered_hashes.append(text_to_hashes[line][idx])
                used_count[line] = idx + 1
    return ordered_hashes

def _get_default_params(output_format):
    cp = {}
    cp['crf'] = cfg.get("h264_crf", 18)
    cp['preset'] = 'fast'
    cp['gop'] = 1
    cp['prores_profile'] = cfg.get("prores_profile", "lt")
    cp['audio_codec'] = 'aac'
    cp['audio_bitrate'] = '256k'
    return cp


# Profile routes
@app.route('/profiles')
def list_profiles():
    profiles = []
    if os.path.isdir(PROFILES_DIR):
        for fname in os.listdir(PROFILES_DIR):
            if fname.endswith('.ffprofile'):
                name = fname[:-len('.ffprofile')]
                profiles.append(name)
    return jsonify(profiles)

@app.route('/profiles/load/<name>')
def load_profile(name):
    path = os.path.join(PROFILES_DIR, name + '.ffprofile')
    if not os.path.exists(path):
        return jsonify({'error': 'Profile not found'}), 404
    with open(path, 'r') as f:
        return jsonify(json.load(f))

@app.route('/profiles/save', methods=['POST'])
def save_profile():
    data = request.get_json()
    name = data.get('name')
    if not name:
        return jsonify({'error': 'Profile name required'}), 400
    params = data.get('params', {})
    path = os.path.join(PROFILES_DIR, name + '.ffprofile')
    with open(path, 'w') as f:
        json.dump(params, f, indent=2)
    return jsonify({'status': 'saved'})
    
@app.route('/profiles/delete/<name>', methods=['POST'])
def delete_profile(name):
    path = os.path.join(PROFILES_DIR, name + '.ffprofile')
    if not os.path.exists(path):
        return jsonify({'error': 'Profile not found'}), 404
    os.remove(path)
    return jsonify({'status': 'deleted'})


# Step 1: Transcription
def process_step1(job_id, job_dir, video_bytes, start_time, end_time, original_ext, video_filename):
    video_raw = os.path.join(job_dir, "temp", f"source_video{original_ext}")
    paths.set_work_dir(job_dir)
    try:
        with open(video_raw, "wb") as f:
            f.write(video_bytes)
        with open(os.path.join(job_dir, "video_name.txt"), "w") as vf:
            vf.write(video_filename)
        if start_time is not None or end_time is not None:
            trimmed = os.path.join(job_dir, "temp", "trimmed.mp4")
            cmd = [FFMPEG, "-y", "-i", video_raw]
            if start_time:
                cmd += ["-ss", str(start_time)]
            if end_time:
                dur = end_time - (start_time or 0)
                cmd += ["-t", str(dur)]
            cmd += ["-c", "copy", trimmed]
            subprocess.run(cmd, check=True)
            shutil.move(trimmed, video_raw)

        # Extract media info
        try:
            probe_exe = cfg.get("ffprobe_path", "ffprobe")
            probe_cmd = [
                probe_exe, "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", video_raw
            ]
            probe_output = subprocess.check_output(probe_cmd, text=True)
            info = json.loads(probe_output)
            duration = float(info['format']['duration'])
            v_stream = next((s for s in info['streams'] if s['codec_type'] == 'video'), None)
            if v_stream:
                codec = v_stream.get('codec_name', 'unknown')
                width = v_stream.get('width', '?')
                height = v_stream.get('height', '?')
                _write_log(job_id, f"Source media: {codec} {width}x{height}, {duration:.1f}s")
            else:
                _write_log(job_id, f"Source media: audio-only, duration {duration:.1f}s")
        except Exception as e:
            _write_log(job_id, f"Media info unavailable: {e}")

        from faster_whisper import WhisperModel
        device = cfg.get("whisper_device", "cpu")
        compute = cfg.get("whisper_compute_type", "int8")
        try:
            model = WhisperModel(cfg.get("whisper_model", "base"),
                                 device=device,
                                 compute_type=compute)
        except Exception:
            device = "cpu"
            compute = "int8"
            model = WhisperModel(cfg.get("whisper_model", "base"),
                                 device="cpu",
                                 compute_type="int8")
        actual_device = device
        with jobs_lock:
            jobs[job_id]['message'] = f'Transcribing with faster-whisper ({cfg.get("whisper_model", "base")}, {actual_device}, {compute})...'
        _write_log(job_id, f'Whisper device: {actual_device}, compute: {compute}')
        segments, info = model.transcribe(video_raw, word_timestamps=True)
        detected_lang = info.language
        with open(os.path.join(job_dir, "detected_lang.txt"), "w") as f:
            f.write(detected_lang)
        log.info(f"Detected language: {detected_lang}")

        seg_list = [{'text': seg.text.strip(), 'start': seg.start, 'end': seg.end} for seg in segments]
        anchors = generate_anchors(seg_list)
        save_anchors(anchors, job_dir)
        _write_log(job_id, 'Transcription completed')

        narrative_lines = [a['text'] for a in anchors]
        clean_narrative = "\n".join(narrative_lines)

        with jobs_lock:
            jobs[job_id].update({
                "status": "ready_for_edit",
                "step": 1,
                "narrative": clean_narrative,
                "progress": 1.0,
                "video_name": video_filename,
                "original_narrative": clean_narrative
            })
    except Exception as e:
        _write_log(job_id, f'Transcription failed: {e}')
        with jobs_lock:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = str(e)


# Step 2: Cutting
def process_step2(job_id, job_dir, edited_narrative, output_format='prores', output_container='mov',
                  keep_intro=False, keep_outro=False, custom_params=None):
    paths.set_work_dir(job_dir)
    _write_log(job_id, 'Cut started')
    ordered_hashes = _apply_edit_from_map(job_dir, edited_narrative)
    if not ordered_hashes:
        _write_log(job_id, 'No matching lines found after edit')
        with jobs_lock:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = "No matching lines found after edit"
        return
    order_path = paths.project_order()
    with open(order_path, 'w') as f:
        json.dump(ordered_hashes, f)
    text_path = paths.project_text()
    with open(text_path, 'w', encoding='utf-8') as f:
        f.write(edited_narrative)
    _write_log(job_id, 'project.txt updated')
    log_lines = []

    def progress_callback(percent, message):
        with jobs_lock:
            if message and not message.startswith("ffmpeg "):
                jobs[job_id].update({
                    "progress": percent / 100.0,
                    "message": message
                })
            else:
                jobs[job_id].update({
                    "progress": percent / 100.0
                })
        if message and (not log_lines or log_lines[-1] != message):
            log_lines.append(message)
            _write_log(job_id, message)

    try:
        render.cut(ordered_hashes=ordered_hashes,
                   output_format=output_format,
                   output_container=output_container,
                   keep_intro=keep_intro,
                   keep_outro=keep_outro,
                   progress_callback=progress_callback,
                   custom_params=custom_params)
        out_dir = os.path.join(job_dir, "out")
        final_video = None
        for f in os.listdir(out_dir):
            if f.startswith('final.') and not f.endswith('.srt'):
                final_video = os.path.join(out_dir, f)
                break
        final_srt = None
        for f in os.listdir(out_dir):
            if f.startswith('final.') and f.endswith('.srt'):
                final_srt = os.path.join(out_dir, f)
                log.info(f"Found SRT: {_relative(final_srt)}")
                break
        files = []
        sizes = {}
        srt_content = None
        if final_video and os.path.exists(final_video):
            files.append(os.path.basename(final_video))
            sizes[os.path.basename(final_video)] = f"{os.path.getsize(final_video)/1024/1024:.1f} MB"
            log.info(f"Final video: {_relative(final_video)}")
        else:
            log.warning(f"No final video found in {_relative(out_dir)}")
        if final_srt and os.path.exists(final_srt):
            files.append(os.path.basename(final_srt))
            sizes[os.path.basename(final_srt)] = f"{os.path.getsize(final_srt)/1024:.0f} KB"
            try:
                with open(final_srt, "r", encoding="utf-8") as f:
                    srt_content = f.read()
                log.info(f"SRT content length: {len(srt_content)} chars")
            except Exception as e:
                log.error(f"Failed to read SRT: {e}")
        else:
            log.warning(f"No SRT file found in {_relative(out_dir)}")
        ok, review = check.review(work_dir=job_dir, verbose=False)
        _write_log(job_id, 'Cut completed')
        with jobs_lock:
            jobs[job_id].update({
                "status": "review",
                "step": 2,
                "files": files,
                "sizes": sizes,
                "review": review,
                "srt_content": srt_content,
                "progress": 1.0,
                "log_lines": log_lines
            })
    except Exception as e:
        _write_log(job_id, f'Cut failed: {e}')
        with jobs_lock:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = str(e)


# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/jobs')
def list_jobs():
    jobs_list = []
    if os.path.isdir(RESULTS_ROOT):
        for job_id in os.listdir(RESULTS_ROOT):
            job_path = os.path.join(RESULTS_ROOT, job_id)
            if os.path.isdir(job_path):
                video_name = "unknown"
                video_name_file = os.path.join(job_path, "video_name.txt")
                if os.path.exists(video_name_file):
                    with open(video_name_file, "r") as f:
                        video_name = f.read().strip()
                else:
                    temp_dir = os.path.join(job_path, "temp")
                    if os.path.isdir(temp_dir):
                        for f in os.listdir(temp_dir):
                            if f.startswith("source_video."):
                                video_name = f
                                break
                has_project = os.path.exists(os.path.join(job_path, "project.txt"))
                jobs_list.append({
                    "id": job_id,
                    "video_name": video_name,
                    "has_project": has_project
                })
    return jsonify(jobs_list)

@app.route('/step1', methods=['POST'])
def step1():
    job_id = uuid.uuid4().hex[:12]
    job_dir = _make_job_dir(job_id)
    video = request.files.get('video')
    if not video:
        return jsonify({'error': 'Video required'}), 400
    video_bytes = video.read()
    original_ext = os.path.splitext(video.filename)[1].lower() or '.mp4'
    video_filename = video.filename
    start = request.form.get('start')
    end = request.form.get('end')
    try:
        start = float(start) if start else None
    except:
        start = None
    try:
        end = float(end) if end else None
    except:
        end = None
    with jobs_lock:
        jobs[job_id] = {
            'status': 'processing',
            'step': 1,
            'progress': 0.0,
            'message': 'Processing...',
            'work_dir': job_dir,
            'video_name': video_filename
        }
    threading.Thread(target=process_step1, args=(job_id, job_dir, video_bytes, start, end, original_ext, video_filename), daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/step2', methods=['POST'])
def step2():
    job_id = request.form.get('job_id')
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404
        job_dir = job['work_dir']
    narrative = request.form.get('narrative', '')
    output_format = request.form.get('output_format', 'prores')
    output_container = request.form.get('output_container', 'mov')
    keep_intro = request.form.get('keep_intro', '0') == '1'
    keep_outro = request.form.get('keep_outro', '0') == '1'
    custom_params = None
    custom_json = request.form.get('custom_params')
    if custom_json:
        try:
            custom_params = json.loads(custom_json)
        except:
            pass
    with jobs_lock:
        jobs[job_id]['status'] = 'cutting'
        jobs[job_id]['message'] = 'Cutting...'
        jobs[job_id]['progress'] = 0.0
    threading.Thread(target=process_step2, args=(job_id, job_dir, narrative, output_format, output_container, keep_intro, keep_outro, custom_params), daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/status/<job_id>')
def status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            job_dir = os.path.join(RESULTS_ROOT, job_id)
            if os.path.isdir(job_dir):
                _load_existing_jobs()
                job = jobs.get(job_id)
        if not job:
            return jsonify({'error': 'Not found'}), 404
    resp = {
        'job_id': job_id,
        'status': job.get('status'),
        'step': job.get('step', 1),
        'progress': job.get('progress', 0.0),
        'message': job.get('message', '')
    }
    if job.get('narrative'):
        resp['narrative'] = job['narrative']
    if job.get('original_narrative'):
        resp['original_narrative'] = job['original_narrative']
    if job.get('files'):
        resp['files'] = job['files']
        resp['sizes'] = job.get('sizes', {})
    if job.get('review'):
        resp['review'] = job['review']
    if job.get('srt_content'):
        resp['srt_content'] = job['srt_content']
    if job.get('log_lines'):
        resp['log_lines'] = job['log_lines']
    if job.get('error'):
        resp['error'] = job['error']
    return jsonify(resp)

@app.route('/log/<job_id>')
def get_log(job_id):
    log_file = os.path.join(RESULTS_ROOT, job_id, "process.log")
    if not os.path.exists(log_file):
        return jsonify({'log': ''})
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
    return jsonify({'log': content})

@app.route('/download/<job_id>/<filename>')
def download(job_id, filename):
    if not filename.startswith('final.'):
        return 'Forbidden', 403
    file_path = os.path.join(RESULTS_ROOT, job_id, 'out', filename)
    if os.path.exists(file_path):
        return send_from_directory(os.path.dirname(file_path), filename)
    return 'File not found', 404

@app.route('/accept/<job_id>', methods=['POST'])
def accept(job_id):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]['status'] = 'completed'
    return jsonify({'status': 'accepted'})

@app.route('/project/<job_id>')
def download_project(job_id):
    job_dir = os.path.join(RESULTS_ROOT, job_id)
    proj_path = os.path.join(job_dir, "project.txt")
    if not os.path.exists(proj_path):
        return "Project file not found", 404
    return send_from_directory(job_dir, "project.txt", as_attachment=True)

@app.route('/export/<job_id>/<format>')
def export_timeline(job_id, format):
    if format not in ('edl', 'xml', 'otio'):
        return "Invalid format", 400
    job_dir = os.path.join(RESULTS_ROOT, job_id)
    paths.set_work_dir(job_dir)
    order_path = paths.project_order()
    if not os.path.exists(order_path):
        order_path = os.path.join(job_dir, "project_order.json")
    if not os.path.exists(order_path):
        return "Project order not found", 404
    with open(order_path) as f:
        ordered_hashes = json.load(f)
    out_file = os.path.join(job_dir, "out", f"timeline.{format}")
    try:
        from texideo_core import export
        export.export(job_dir, format, out_file, ordered_hashes, reel_name="VIDEO")
    except Exception as e:
        return f"Export failed: {e}", 500
    return send_from_directory(os.path.dirname(out_file), f"timeline.{format}", as_attachment=True)

@app.route('/video_info', methods=['POST'])
def video_info():
    if 'video' not in request.files:
        return jsonify({'error': 'No video file'}), 400
    video = request.files['video']
    tmp_path = os.path.join(tempfile.gettempdir(), 'texideo_info_' + uuid.uuid4().hex)
    video.save(tmp_path)
    try:
        dur = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", tmp_path
        ], text=True).strip()
        os.remove(tmp_path)
        return jsonify({'duration': float(dur)})
    except:
        os.remove(tmp_path)
        return jsonify({'error': 'Could not read video'}), 500


# Config routes
@app.route('/download/config')
def download_config():
    cfg_path = os.path.join(BASE_DIR, "config.cfg")
    if not os.path.exists(cfg_path):
        return "config.cfg not found", 404
    with open(cfg_path, 'r', encoding='utf-8') as f:
        cfg_data = json.load(f)
    cfg_data['_comment'] = "Save this file in the Texideo project root (same folder as texideo.py)."
    cfg_text = json.dumps(cfg_data, indent=2, ensure_ascii=False)
    return Response(cfg_text, mimetype='application/json',
                    headers={"Content-Disposition": "attachment; filename=config.cfg"})

@app.route('/config/load')
def load_config_route():
    cfg_path = os.path.join(BASE_DIR, "config.cfg")
    if not os.path.exists(cfg_path):
        return jsonify(cfg)
    with open(cfg_path, 'r', encoding='utf-8') as f:
        return jsonify(json.load(f))

@app.route('/config/save', methods=['POST'])
def save_config_route():
    cfg_path = os.path.join(BASE_DIR, "config.cfg")
    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'error': 'No data received'}), 400
    try:
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        global cfg
        cfg = load_config()
        return jsonify({'status': 'saved'})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/config/reset', methods=['POST'])
def reset_config_route():
    return jsonify({'status': 'reset', 'defaults': DEFAULT_CONFIG})


# Job deletion
@app.route('/job/<job_id>/delete', methods=['POST'])
def delete_job(job_id):
    job_dir = os.path.join(RESULTS_ROOT, job_id)
    if not os.path.isdir(job_dir):
        return jsonify({'status': 'error', 'error': 'Job not found'}), 404
    try:
        shutil.rmtree(job_dir)
        with jobs_lock:
            if job_id in jobs:
                del jobs[job_id]
        return jsonify({'status': 'deleted'})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/jobs/delete_all', methods=['POST'])
def delete_all_jobs():
    if not os.path.isdir(RESULTS_ROOT):
        return jsonify({'status': 'error', 'error': 'No jobs directory'}), 404
    count = 0
    for job_id in os.listdir(RESULTS_ROOT):
        job_dir = os.path.join(RESULTS_ROOT, job_id)
        if os.path.isdir(job_dir):
            try:
                shutil.rmtree(job_dir)
                count += 1
            except Exception as e:
                log.error(f"Failed to delete {job_id}: {e}")
    with jobs_lock:
        jobs.clear()
    return jsonify({'status': 'deleted', 'count': count})

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)