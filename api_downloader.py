import os
import tempfile
import shutil
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from pytubefix import YouTube
from pytubefix import YouTube
from pytubefix.exceptions import PytubeFixError
YouTube.use_progressive_token = True


app = Flask(__name__)
CORS(app)  # libera para acessar desde o frontend (ajuste em produção)

# --- Helpers ---
def yt_from_url(url):
    try:
        yt = YouTube(url)
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
    # tenta extrair campos comuns; nem todos streams terão todos os atributos
    filesize_formatted = format_bytes(getattr(s, "filesize", None))
    return {
        "itag": getattr(s, "itag", None),
        "mime_type": getattr(s, "mime_type", None),
        "type": getattr(s, "type", None),        # 'video' ou 'audio'
        "resolution": getattr(s, "resolution", None),  # e.g. '720p' ou None
        "fps": getattr(s, "fps", None),
        "abr": getattr(s, "abr", None),          # audio bitrate e.g. '128kbps'
        "is_progressive": getattr(s, "is_progressive", None),
        "filesize": filesize_formatted, # bytes (pode ser None), now formatted
        "is_adaptive": getattr(s, "is_adaptive", None),
        "mime_subtype": getattr(s, "mime_subtype", None)
    }

# --- Endpoints ---

@app.route("/info", methods=["GET"])
def info():
    """
    GET /info?url=<youtube_url>
    Retorna: title, duration (segundos), author, thumbnails (lista), video_id
    """
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Falta parâmetro 'url'"}), 400
    try:
        yt = yt_from_url(url)
        data = {
            "title": yt.title,
            "author": getattr(yt, "author", None),
            "length": getattr(yt, "length", None),  # segundos
            "views": getattr(yt, "views", None),
            "description": getattr(yt, "description", None),
            "thumbnails": getattr(yt, "thumbnails", None),
            "video_id": yt.video_id # Adicionado o video_id aqui
        }
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": "Não foi possível obter info do vídeo", "detail": str(e)}), 500

@app.route("/streams", methods=["GET"])
def streams():
    """
    GET /streams?url=<youtube_url>
    Retorna: lista de streams (itag, resolution, mime_type, filesize, abr, type)
    """
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Falta parâmetro 'url'"}), 400
    try:
        yt = yt_from_url(url)
        streams = []
        # yt.streams é iterável; mapeamos para dicionários
        for s in yt.streams:
            streams.append(stream_to_dict(s))
        # ordena streams úteis: progressivos (video+audio) primeiro, depois video-only por resolução
        def sort_key(s):
            # Prioriza progressive streams (video+audio)
            prog_priority = 0 if s.get("is_progressive") else 1

            # Extrai resolução numérica para ordenação
            res_str = s.get("resolution")
            resolution_num = 0
            if res_str and res_str.endswith("p"):
                try:
                    resolution_num = int(res_str[:-1])
                except ValueError:
                    pass # Keep 0 if conversion fails

            # Prioriza 'video' type over 'audio' if not progressive
            type_priority = 0 if s.get("type") == "video" else 1

            # Prioriza maior bitrate para áudio
            abr_str = s.get("abr")
            abr_num = 0
            if abr_str and abr_str.endswith("kbps"):
                try:
                    abr_num = int(abr_str[:-4])
                except ValueError:
                    pass

            # Sorting logic:
            # 1. Progressive first (0)
            # 2. Then by type (video before audio if not progressive)
            # 3. For video, higher resolution first (negative for descending)
            # 4. For audio, higher bitrate first (negative for descending)
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
    """
    GET /download?url=<youtube_url>&itag=<itag>
    Faz download do stream selecionado e retorna o arquivo (attachment).
    """
    url = request.args.get("url")
    itag = request.args.get("itag")
    if not url or not itag:
        return jsonify({"error": "Parâmetros 'url' e 'itag' são obrigatórios"}), 400
    try:
        yt = yt_from_url(url)
        target_stream = None
        for s in yt.streams:
            if str(getattr(s, "itag", "")) == str(itag):
                target_stream = s
                break
        if target_stream is None:
            return jsonify({"error": "itag não encontrado"}), 404

        # Cria pasta temporária para download
        tmpdir = tempfile.mkdtemp(prefix="yt_dl_")
        try:
            # Determina extensão aproximada
            ext = "mp4" # Default for most video streams
            mime_type = getattr(target_stream, "mime_type", "")
            if "audio" in mime_type and "mp4" not in mime_type:
                ext = "m4a" # For audio-only streams
            elif "video" in mime_type and "webm" in mime_type:
                ext = "webm" # For webm video streams

            # Clean up title for filename
            filename_safe = "".join(c for c in yt.title if c.isalnum() or c in " ._-").strip()
            # Ensure filename doesn't exceed common limits and remove trailing dots/spaces
            filename_safe = filename_safe[:100].strip().rstrip(' .')
            outname = f"{filename_safe}_{itag}.{ext}"

            # download usando pytubefix stream.download
            target_stream.download(output_path=tmpdir, filename=outname)
            filepath = os.path.join(tmpdir, outname)

            # Fallback if the filename wasn't exactly as expected (though pytubefix is usually good)
            if not os.path.exists(filepath):
                files = os.listdir(tmpdir)
                if files:
                    # Find the newest file in the temporary directory
                    files = sorted(files, key=lambda f: os.path.getmtime(os.path.join(tmpdir, f)), reverse=True)
                    filepath = os.path.join(tmpdir, files[0])
                else:
                    raise FileNotFoundError("O arquivo de download não foi criado.")

            # envia o arquivo como attachment
            return send_file(filepath, as_attachment=True, download_name=outname)
        finally:
            # remove arquivos temporários
            try:
                shutil.rmtree(tmpdir)
            except Exception as e:
                print(f"Erro ao remover pasta temporária {tmpdir}: {e}")

    except Exception as e:
        return jsonify({"error": "Erro durante download", "detail": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

