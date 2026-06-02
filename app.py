import os
import re
import uuid
import tempfile
import threading
import time
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app, origins="*")

DOWNLOADS = {}
LOCK = threading.Lock()

def cleanup_file(path, delay=300):
    def _delete():
        time.sleep(delay)
        try:
            if os.path.exists(path):
                os.remove(path)
        except:
            pass
    threading.Thread(target=_delete, daemon=True).start()

def do_download(job_id, url, fmt, quality):
    tmpdir = tempfile.mkdtemp()

    ext_map = {
        "MP3": "mp3", "WAV": "wav", "FLAC": "flac",
        "AIFF": "aiff", "M4A": "m4a"
    }
    codec = ext_map.get(fmt.upper(), "mp3")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": codec,
            "preferredquality": quality if fmt == "MP3" else "0",
        }],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "track")

        files = os.listdir(tmpdir)
        if not files:
            raise Exception("No file downloaded")

        filepath = os.path.join(tmpdir, files[0])
        filename = files[0]

        with LOCK:
            DOWNLOADS[job_id] = {
                "status": "done",
                "file_path": filepath,
                "filename": filename,
                "title": title,
            }
        cleanup_file(filepath)

    except Exception as e:
        with LOCK:
            DOWNLOADS[job_id] = {"status": "error", "error": str(e)}

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "Drag&Play Backend v2"})

@app.route("/info", methods=["POST"])
def get_info():
    data = request.json or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL"}), 400
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            return jsonify({
                "type": "playlist",
                "title": info.get("title", "Playlist"),
                "count": len(entries),
                "tracks": [{"title": e.get("title","Unknown"), "url": e.get("webpage_url") or e.get("url",""), "duration": e.get("duration", 0)} for e in entries],
            })
        return jsonify({
            "type": "track",
            "title": info.get("title", "Unknown"),
            "duration": info.get("duration", 0),
            "uploader": info.get("uploader", ""),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/download", methods=["POST"])
def start_download():
    data = request.json or {}
    url = data.get("url", "").strip()
    fmt = data.get("format", "MP3").upper()
    quality = data.get("quality", "320")
    if not url:
        return jsonify({"error": "No URL"}), 400
    job_id = str(uuid.uuid4())
    with LOCK:
        DOWNLOADS[job_id] = {"status": "downloading"}
    threading.Thread(target=do_download, args=(job_id, url, fmt, quality), daemon=True).start()
    return jsonify({"job_id": job_id, "status": "downloading"})

@app.route("/status/<job_id>", methods=["GET"])
def check_status(job_id):
    with LOCK:
        job = DOWNLOADS.get(job_id)
    if not job:
        return jsonify({"error": "Not found"}), 404
    if job["status"] == "done":
        return jsonify({"status": "done", "filename": job.get("filename"), "title": job.get("title")})
    elif job["status"] == "error":
        return jsonify({"status": "error", "error": job.get("error")})
    return jsonify({"status": "downloading"})

@app.route("/file/<job_id>", methods=["GET"])
def get_file(job_id):
    with LOCK:
        job = DOWNLOADS.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "Not ready"}), 404
    filepath = job.get("file_path")
    if not filepath or not os.path.exists(filepath):
        return jsonify({"error": "File gone"}), 404
    return send_file(filepath, as_attachment=True, download_name=job.get("filename", "track.mp3"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
