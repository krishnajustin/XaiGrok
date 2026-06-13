"""
xAI Grok Imagine backend for the AI Studio.

All three tabs use the xAI Imagine API (api.x.ai) — no RunPod, no endpoints,
just one xAI API key.

  - edit  (Edit)    -> POST /v1/images/edits   (source image + instruction)
  - i2i   (Restyle) -> POST /v1/images/edits   (source image + style prompt)
  - i2v   (Video)   -> POST /v1/videos/generations  (async: start + poll)

Docs: https://docs.x.ai/developers/model-capabilities/imagine
The API key is passed per-request (never written to disk).
"""

import time
import requests

XAI_BASE = "https://api.x.ai/v1"
IMAGE_MODEL = "grok-imagine-image-quality"
VIDEO_MODEL = "grok-imagine-video"

POLL_INTERVAL = 3
POLL_MAX = 300
REQUEST_TIMEOUT = 180


class ProviderError(Exception):
    pass


# Kept so /api/providers keeps working (the UI no longer needs endpoint IDs).
REGISTRY = {
    "xai": {
        "label": "xAI Grok Imagine",
        "key_hint": "xAI API key (console.x.ai → API Keys, starts with xai-)",
        "models": [
            {"id": "grok-imagine", "label": "Grok Imagine", "tasks": ["edit", "i2i", "i2v"]},
        ],
    },
}


def public_registry():
    return {
        pid: {"label": p["label"], "key_hint": p["key_hint"], "models": p["models"]}
        for pid, p in REGISTRY.items()
    }


def _headers(api_key):
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _data_uri(image):
    """Frontend sends a data: URL already; ensure the prefix is present."""
    if isinstance(image, str) and image.startswith("data:"):
        return image
    return "data:image/png;base64," + (image or "")


def _parse_image_response(j):
    urls = []
    for item in j.get("data", []):
        if item.get("b64_json"):
            urls.append("data:image/png;base64," + item["b64_json"])
        elif item.get("url"):
            urls.append(item["url"])
    if not urls:
        raise ProviderError("xAI returned no image. Raw: " + str(j)[:400])
    return urls


# ---------------------------------------------------------------------------
# Image edit / restyle  (same endpoint, prompt is what differs)
# ---------------------------------------------------------------------------
def xai_edit(api_key, prompt, image, opts):
    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt or "",
        "image": {"url": _data_uri(image), "type": "image_url"},
    }
    if opts.get("resolution"):
        payload["resolution"] = opts["resolution"]
    if opts.get("n"):
        payload["n"] = int(opts["n"])
    r = requests.post(f"{XAI_BASE}/images/edits", json=payload,
                      headers=_headers(api_key), timeout=REQUEST_TIMEOUT)
    if r.status_code >= 400:
        raise ProviderError(f"xAI /images/edits {r.status_code}: {r.text[:400]}")
    urls = _parse_image_response(r.json())
    return {"type": "image", "url": urls[0], "urls": urls}


def xai_generate(api_key, prompt, opts):
    """Text-to-image: POST /v1/images/generations."""
    payload = {"model": IMAGE_MODEL, "prompt": prompt or ""}
    if opts.get("aspect_ratio"):
        payload["aspect_ratio"] = opts["aspect_ratio"]
    if opts.get("resolution"):
        payload["resolution"] = opts["resolution"]
    if opts.get("n"):
        payload["n"] = int(opts["n"])
    r = requests.post(f"{XAI_BASE}/images/generations", json=payload,
                      headers=_headers(api_key), timeout=REQUEST_TIMEOUT)
    if r.status_code >= 400:
        raise ProviderError(f"xAI /images/generations {r.status_code}: {r.text[:400]}")
    urls = _parse_image_response(r.json())
    return {"type": "image", "url": urls[0], "urls": urls}


# ---------------------------------------------------------------------------
# Image -> video  (async: start, then poll)
# ---------------------------------------------------------------------------
def xai_video_start(api_key, prompt, image, opts):
    """Kick off video generation, return immediately (browser polls status).
    Returns {"done": True, "url": ...} if finished instantly, else
    {"done": False, "request_id": ...}."""
    opts = opts or {}
    payload = {
        "model": VIDEO_MODEL,
        "prompt": prompt or "",
        "image": {"url": _data_uri(image), "type": "image_url"},
    }
    if opts.get("aspect_ratio"):
        payload["aspect_ratio"] = opts["aspect_ratio"]
    if opts.get("resolution"):
        payload["resolution"] = opts["resolution"]

    r = requests.post(f"{XAI_BASE}/videos/generations", json=payload,
                      headers=_headers(api_key), timeout=REQUEST_TIMEOUT)
    if r.status_code >= 400:
        raise ProviderError(f"xAI /videos/generations {r.status_code}: {r.text[:400]}")
    j = r.json()
    url = _video_url(j)
    if url:
        return {"done": True, "url": url}
    request_id = j.get("request_id") or j.get("id")
    if not request_id:
        raise ProviderError("xAI video: no request_id. Raw: " + str(j)[:400])
    return {"done": False, "request_id": request_id}


def xai_video_status(api_key, request_id):
    """One status check (short). Returns {"status": "processing"} or
    {"status": "done", "url": ...}."""
    if not request_id:
        raise ProviderError("Missing request_id.")
    g = requests.get(f"{XAI_BASE}/videos/{request_id}",
                     headers=_headers(api_key), timeout=60).json()
    status = (g.get("status") or "").lower()
    if status in ("done", "completed", "succeeded"):
        url = _video_url(g)
        if not url:
            raise ProviderError("xAI video finished but no URL. Raw: " + str(g)[:400])
        return {"status": "done", "url": url}
    if status in ("failed", "error", "cancelled"):
        raise ProviderError("xAI video failed: " + str(g)[:400])
    return {"status": "processing"}


def _video_url(j):
    v = j.get("video")
    if isinstance(v, dict) and v.get("url"):
        return v["url"]
    if j.get("url"):
        return j["url"]
    data = j.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("url"):
        return data[0]["url"]
    return None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def run_task(task, provider, model, api_key, prompt=None, image=None, images=None, opts=None):
    opts = opts or {}
    if not api_key:
        raise ProviderError("No xAI API key set. Add it in ⚙ Settings.")

    imgs = [i for i in ([*(images or []), image] if images else [image]) if i]
    first = imgs[0] if imgs else None

    # Text-to-image: no source image needed
    if task == "generate":
        return xai_generate(api_key, prompt, opts)

    if not first:
        raise ProviderError("Upload a source image first.")
    # Video uses the async /api/video/* endpoints, not run_task.
    return xai_edit(api_key, prompt, first, opts)
