"""
AI Studio — Flask backend (xAI Grok Imagine).

Runs locally (python app.py) and on Vercel (api/index.py imports `app`).
The xAI API key is entered in the browser and sent per request; it is never
written to disk on the server.
"""

import os
import tempfile
import base64
import uuid
import requests
from flask import Flask, request, jsonify, send_from_directory, Response

import providers

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")

# xAI returns image/video URLs, so we usually don't persist anything. When a
# result IS an inline data: URL, we try to save it to a writable dir for a
# small response. On read-only/serverless filesystems this is skipped and the
# data: URL is returned as-is (the browser still renders it fine).
OUTPUT_DIR = os.path.join(STATIC_DIR, "outputs")
try:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    _CAN_WRITE = os.access(OUTPUT_DIR, os.W_OK)
except OSError:
    _CAN_WRITE = False


def _persist_data_urls(result):
    """Best-effort: turn inline data: URLs into /static/outputs/<file> URLs.
    Falls back to leaving the data: URL untouched if the FS isn't writable."""
    def save(data_url):
        if not isinstance(data_url, str) or not data_url.startswith("data:"):
            return data_url
        if not _CAN_WRITE:
            return data_url  # serverless / read-only — let the browser use it directly
        try:
            header, b64 = data_url.split(",", 1)
            ext = "mp4" if "video" in header else "png"
            name = f"{uuid.uuid4().hex}.{ext}"
            with open(os.path.join(OUTPUT_DIR, name), "wb") as f:
                f.write(base64.b64decode(b64))
            return f"/static/outputs/{name}"
        except Exception:
            return data_url

    if isinstance(result, dict):
        if "url" in result:
            result["url"] = save(result["url"])
        if "urls" in result and isinstance(result["urls"], list):
            result["urls"] = [save(u) for u in result["urls"]]
        result.pop("raw", None)
    return result


def _resolve_key(body):
    """Use the browser-provided key if present, else fall back to the
    XAI_API_KEY environment variable (set in Vercel project settings)."""
    return (body.get("api_key") or "").strip() or os.environ.get("XAI_API_KEY", "").strip()


@app.route("/")
def index():
    # Return HTML with an explicit content-type. send_from_directory uses a
    # streaming response whose Content-Type can get dropped by Vercel's WSGI
    # bridge, which makes the browser download the page instead of rendering it.
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html")


@app.route("/api/providers")
def get_providers():
    return jsonify(providers.public_registry())


@app.route("/api/run", methods=["POST"])
def run():
    body = request.get_json(force=True)
    task = body.get("task")
    provider = body.get("provider")
    model = body.get("model")
    api_key = _resolve_key(body)
    prompt = body.get("prompt")
    image = body.get("image")          # single data URL or base64 (back-compat)
    images = body.get("images")        # list of data URLs (multi-reference)
    opts = body.get("opts") or {}

    if task not in ("i2i", "edit", "generate", "enhance", "i2v"):
        return jsonify({"error": f"Unknown task '{task}'"}), 400
    try:
        result = providers.run_task(task, provider, model, api_key,
                                    prompt=prompt, image=image, images=images, opts=opts)
        result = _persist_data_urls(result)
        return jsonify(result)
    except providers.ProviderError as e:
        return jsonify({"error": str(e)}), 400
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Network error: {e}"}), 502
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {e}"}), 500


@app.route("/api/video/start", methods=["POST"])
def video_start():
    body = request.get_json(force=True)
    api_key = _resolve_key(body)
    try:
        out = providers.xai_video_start(api_key, body.get("prompt"),
                                        body.get("image"), body.get("opts") or {})
        return jsonify(out)
    except providers.ProviderError as e:
        return jsonify({"error": str(e)}), 400
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Network error: {e}"}), 502
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {e}"}), 500


@app.route("/api/video/status", methods=["POST"])
def video_status():
    body = request.get_json(force=True)
    api_key = _resolve_key(body)
    try:
        out = providers.xai_video_status(api_key, body.get("request_id"))
        return jsonify(out)
    except providers.ProviderError as e:
        return jsonify({"error": str(e)}), 400
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Network error: {e}"}), 502
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {e}"}), 500


@app.route("/api/proxy")
def proxy():
    """Stream a remote image/video through the server so the browser can
    display & download it without CORS issues."""
    url = request.args.get("url")
    if not url:
        return "missing url", 400
    if not (url.startswith("http://") or url.startswith("https://")):
        return "bad url", 400
    try:
        r = requests.get(url, stream=True, timeout=120)
        ctype = r.headers.get("Content-Type", "application/octet-stream")
        return Response(r.iter_content(chunk_size=8192), content_type=ctype)
    except requests.exceptions.RequestException as e:
        return f"proxy error: {e}", 502


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"\n  AI Image & Video Studio running →  http://127.0.0.1:{port}\n")
    app.run(host="127.0.0.1", port=port, debug=False)
