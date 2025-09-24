import os
import tempfile
import shutil
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from pytubefix import YouTube
from pytubefix.innertube import InnerTube

app = Flask(__name__)
CORS(app)

# Configurar o InnerTube para evitar detecção de bot
InnerTube.use_oauth = False
InnerTube.use_progressive = True
InnerTube.client = "ANDROID"  # ou "WEB" se necessário

# --- Helpers ---
def yt_from_url(url):
    try:
        # Configurar opções específicas para evitar detecção
        yt = YouTube(
            url,
            use_oauth=False,
            allow_oauth_cache=True,
            use_progressive=True
        )
        return yt
    except Exception as e:
        raise

def format_bytes(size_bytes):
    """Converts a size in bytes to a human-readable format."""
    if size_bytes is None:
        return "N/A"
    if size_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = 0
    while size_bytes >= 1024 and i < len(size_name) - 1:
        size_bytes /= 1024
        i += 1
    return f"{size_bytes:.2f} {size_name[i]}"

def stream_to_dict(s):
    filesize_formatted = format_bytes(getattr(s, "filesize", None))
    return {
        "itag": getattr(s, "itag", None),
        "mime_type": getattr(s, "mime_type", None),
        "type": getattr(s, "type", None),
        "resolution": getattr(s, "resolution", None),
        "fps": getattr(s, "fps", None),
        "abr": getattr(s, "abr", None),
        "is_progressive": getattr(s, "is_progressive", None),
        "filesize": filesize_formatted,
        "is_adaptive": getattr(s, "is_adaptive", None),
        "mime_subtype": getattr(s, "mime_subtype", None)
    }

# --- Endpoints ---
@app.route("/info", methods=["GET"])
def info():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Falta parâmetro 'url'"}), 400
    try:
        yt = yt_from_url(url)
        data = {
            "title": yt.title,
            "author": getattr(yt, "author", None),
            "length": getattr(yt, "length", None),
            "views": getattr(yt, "views", None),
            "description": getattr(yt, "description", None),
            "thumbnails": getattr(yt, "thumbnails", None),
            "video_id": yt.video_id
        }
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": "Não foi possível obter info do vídeo", "detail": str(e)}), 500

@app.route("/streams", methods=["GET"])
def streams():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Falta parâmetro 'url'"}), 400
    try:
        yt = yt_from_url(url)
        streams = []
        for s in yt.streams:
            streams.append(stream_to_dict(s))

        def sort_key(s):
            prog_priority = 0 if s.get("is_progressive") else 1
            res_str = s.get("resolution")
            resolution_num = 0
            if res_str and res_str.endswith("p"):
                try:
                    resolution_num = int(res_str[:-1])
                except ValueError:
                    pass
            type_priority = 0 if s.get("type") == "video" else 1
            abr_str = s.get("abr")
            abr_num = 0
            if abr_str and abr_str.endswith("kbps"):
                try:
                    abr_num = int(abr_str[:-4])
                except ValueError:
                    pass
            if s.get("type") == "video" and not s.get("is_progressive"):
                 return (prog_priority, type_priority, -resolution_num, 0)
            elif s.get("type") == "audio" and not s.get("is_progressive"):
                 return (prog_priority, type_priority, 0, -abr_num)
            return (prog_priority, type_priority, -resolution_num, -abr_num)

        streams_sorted = sorted(streams, key=sort_key)
        return jsonify({"title": yt.title, "video_id": yt.video_id, "streams": streams_sorted})
    except Exception as e:
        return jsonify({"error": "Erro ao listar streams", "detail": str(e)}), 500

@app.route("/download", methods=["GET"])
def download():
    url = request.args.get("url")
    itag = request.args.get("itag")
    if not url or not itag:
        return jsonify({"error": "Parâmetros 'url' e 'itag' são obrigatórios"}), 400
    try:
        yt = yt_from_url(url)
        target_stream = yt.streams.get_by_itag(itag)
        if target_stream is None:
            return jsonify({"error": "itag não encontrado"}), 404

        tmpdir = tempfile.mkdtemp(prefix="yt_dl_")
        try:
            ext = "mp4"
            mime_type = getattr(target_stream, "mime_type", "")
            if "audio" in mime_type and "mp4" not in mime_type:
                ext = "m4a"
            elif "video" in mime_type and "webm" in mime_type:
                ext = "webm"
            
            filename_safe = "".join(c for c in yt.title if c.isalnum() or c in " ._-").strip()[:100].strip().rstrip(' .')
            outname = f"{filename_safe}_{itag}.{ext}"

            target_stream.download(output_path=tmpdir, filename=outname)
            filepath = os.path.join(tmpdir, outname)

            if not os.path.exists(filepath):
                files = os.listdir(tmpdir)
                if files:
                    files = sorted(files, key=lambda f: os.path.getmtime(os.path.join(tmpdir, f)), reverse=True)
                    filepath = os.path.join(tmpdir, files[0])
                else:
                    raise FileNotFoundError("O arquivo de download não foi criado.")

            return send_file(filepath, as_attachment=True, download_name=outname)
        finally:
            try:
                shutil.rmtree(tmpdir)
            except Exception as e:
                print(f"Erro ao remover pasta temporária {tmpdir}: {e}")

    except Exception as e:
        return jsonify({"error": "Erro durante download", "detail": str(e)}), 500

# Rota de saúde para testar se o servidor está funcionando
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "Servidor funcionando"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
    except Exception as e:
        return jsonify({"error": "Erro durante download", "detail": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
