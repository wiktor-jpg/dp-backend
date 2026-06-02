import os, uuid, tempfile, threading, time
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app, origins="*")

JOBS = {}
LOCK = threading.Lock()

def cleanup(path, delay=300):
    def _del():
        time.sleep(delay)
        try:
            if os.path.exists(path): os.remove(path)
        except: pass
    threading.Thread(target=_del, daemon=True).start()

def do_download(job_id, url, fmt, quality):
    tmpdir = tempfile.mkdtemp()
    codec = {"MP3":"mp3","WAV":"wav","FLAC":"flac","AIFF":"aiff","M4A":"m4a"}.get(fmt.upper(),"mp3")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{"key":"FFmpegExtractAudio","preferredcodec":codec,"preferredquality":quality if fmt=="MP3" else "0"}],
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title","track")
        files = os.listdir(tmpdir)
        if not files: raise Exception("No file downloaded")
        fp = os.path.join(tmpdir, files[0])
        with LOCK:
            JOBS[job_id] = {"status":"done","file_path":fp,"filename":files[0],"title":title}
        cleanup(fp)
    except Exception as e:
        with LOCK:
            JOBS[job_id] = {"status":"error","error":str(e)}

@app.route("/")
def index():
    return jsonify({"status":"ok","service":"Drag&Play Backend","version":"3.0"})

@app.route("/health")
def health():
    return jsonify({"status":"ok","service":"Drag&Play Backend","version":"3.0"})

@app.route("/info", methods=["POST"])
def info():
    url = (request.json or {}).get("url","").strip()
    if not url: return jsonify({"error":"No URL"}), 400
    try:
        with yt_dlp.YoutubeDL({"quiet":True,"no_warnings":True,"skip_download":True}) as ydl:
            data = ydl.extract_info(url, download=False)
        if data.get("_type") == "playlist":
            entries = [e for e in (data.get("entries") or []) if e]
            return jsonify({"type":"playlist","title":data.get("title","Playlist"),"count":len(entries),
                "tracks":[{"title":e.get("title","Unknown"),"url":e.get("webpage_url") or e.get("url",""),"duration":e.get("duration",0)} for e in entries]})
        return jsonify({"type":"track","title":data.get("title","Unknown"),"duration":data.get("duration",0),"uploader":data.get("uploader","")})
    except Exception as e:
        return jsonify({"error":str(e)}), 400

@app.route("/download", methods=["POST"])
def download():
    d = request.json or {}
    url = d.get("url","").strip()
    if not url: return jsonify({"error":"No URL"}), 400
    job_id = str(uuid.uuid4())
    with LOCK: JOBS[job_id] = {"status":"downloading"}
    threading.Thread(target=do_download, args=(job_id, url, d.get("format","MP3"), d.get("quality","320")), daemon=True).start()
    return jsonify({"job_id":job_id,"status":"downloading"})

@app.route("/status/<job_id>")
def status(job_id):
    with LOCK: job = JOBS.get(job_id)
    if not job: return jsonify({"error":"Not found"}), 404
    if job["status"] == "done": return jsonify({"status":"done","filename":job.get("filename"),"title":job.get("title")})
    if job["status"] == "error": return jsonify({"status":"error","error":job.get("error")})
    return jsonify({"status":"downloading"})

@app.route("/file/<job_id>")
def file(job_id):
    with LOCK: job = JOBS.get(job_id)
    if not job or job["status"] != "done": return jsonify({"error":"Not ready"}), 404
    fp = job.get("file_path")
    if not fp or not os.path.exists(fp): return jsonify({"error":"File gone"}), 404
    return send_file(fp, as_attachment=True, download_name=job.get("filename","track.mp3"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=False)
