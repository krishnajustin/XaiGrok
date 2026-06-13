# Build Plan — AI Image Studio (standalone Android app → APK)

Hand this whole document to an AI coding assistant. It is a complete spec.

## 1. Goal
A **standalone Android app** (installable APK) that lets the user generate and edit
media with their own API keys across multiple providers. Four functions:
1. **Text → Image** (t2i)
2. **Image → Image / edit** (i2i)
3. **Enhance / Upscale** (enhance)
4. **Image → Video** (i2v)

No backend server. All provider calls happen on-device. The user pastes their own
API keys; keys are stored only on the device and sent directly to each provider.

## 2. Tech stack
- **Capacitor 6** wrapping a plain HTML/CSS/JS web app (no framework needed).
- Enable the **CapacitorHttp** plugin so `window.fetch` uses native HTTP — this
  bypasses browser CORS, which otherwise blocks these provider calls.
- `@capacitor/filesystem` + `@capacitor/share` for saving/sharing results.
- Build target: Android. Output: debug APK.

`capacitor.config.json`:
```json
{
  "appId": "com.kr.aistudio",
  "appName": "AI Image Studio",
  "webDir": "www",
  "plugins": { "CapacitorHttp": { "enabled": true } }
}
```

## 3. File structure
```
/
├── capacitor.config.json
├── package.json            # @capacitor/core, cli, android, filesystem, share
└── www/
    ├── index.html          # UI + app logic
    └── providers.js        # provider adapters + model registry
```

## 4. UI requirements (mobile, dark theme)
- Header with app name + a "⚙ Keys" button (shows count of keys set).
- Four tabs: Text→Image, Image→Image, Enhance, Image→Video.
- A controls card: Provider dropdown → Model dropdown (filtered to models that
  support the active tab) → optional image picker (file input, accept=image/*,
  shown only for tabs that need an image) → prompt textarea (hidden for Enhance) →
  per-task options → "Generate" button → inline error area.
- A result card: shows the output `<img>` or `<video>`, with a "Save" button that
  writes the file via Filesystem (Documents dir) and opens the Share sheet; fall
  back to an `<a download>` if Capacitor isn't present.
- Keys modal/sheet: one password field per provider; persist to `localStorage`
  under key `ai_keys` as `{ providerId: key }`. Mask existing values.
- Dropdowns are driven entirely by the registry in section 6 — adding a model there
  must automatically update the menus. Show a warning under the model dropdown if no
  key is set for the chosen provider.

Task option fields:
- t2i: aspect ratio (1:1, 16:9, 9:16, 4:3, 3:4)
- i2i: strength (0–1, default 0.8)
- enhance: upscale factor (2 or 4)
- i2v: none

## 5. Core flow
On Generate: validate (key present; prompt present if required; image present if
required) → show spinner → call `runTask(task, provider, model, key, {prompt, image, opts})`
→ render image/video URL or data URL → enable Save. Show any error message inline.

Images from the picker are read as **data URLs** (base64). Each adapter converts to
the format that provider expects (see below).

## 6. Provider adapters + model registry  (this is the important part)

All requests go through native `fetch`. Generic helpers: POST JSON, GET JSON, and a
poll loop (interval 2s, timeout 300s). Each adapter returns `{ type:"image"|"video", url }`.

### Registry (drives dropdowns)
| Provider id | Label | Auth header | Models (id → tasks) |
|---|---|---|---|
| `replicate` | Replicate | `Authorization: Bearer <key>` | `black-forest-labs/flux-2-pro` (t2i,i2i), `black-forest-labs/flux-2-flex` (t2i,i2i), `black-forest-labs/flux-2-dev` (t2i,i2i), `black-forest-labs/flux-1.1-pro` (t2i), `black-forest-labs/flux-schnell` (t2i), `black-forest-labs/flux-kontext-pro` (i2i), `philz1337x/clarity-upscaler` (enhance), `nightmareai/real-esrgan` (enhance), `kwaivgi/kling-v1.6-standard` (i2v), `wan-video/wan-2.2-i2v-fast` (i2v) |
| `fal` | fal.ai | `Authorization: Key <key>` | `fal-ai/flux-2/dev` (t2i,i2i), `fal-ai/flux/dev` (t2i), `fal-ai/flux/schnell` (t2i), `fal-ai/flux/dev/image-to-image` (i2i), `fal-ai/clarity-upscaler` (enhance), `fal-ai/kling-video/v1/standard/image-to-video` (i2v) |
| `bfl` | Black Forest Labs (Flux) | `x-key: <key>` | `flux-2-pro-preview` (t2i,i2i), `flux-2-pro` (t2i,i2i), `flux-2-flex` (t2i,i2i), `flux-2-max` (t2i,i2i), `flux-2-klein-9b` (t2i,i2i), `flux-pro-1.1` (t2i), `flux-kontext-pro` (i2i) |
| `openai` | OpenAI | `Authorization: Bearer <key>` | `gpt-image-1` (t2i) |
| `xai` | xAI (Grok) | `Authorization: Bearer <key>` | `grok-2-image` (t2i) |

### Replicate
- POST `https://api.replicate.com/v1/models/{model}/predictions` with header
  `Prefer: wait` and body `{ "input": {...} }`.
- Response has `status`; if not terminal, poll `urls.get` until
  `status == "succeeded"`. Output is a URL (or array → take first).
- Inputs:
  - t2i: `{ prompt, aspect_ratio? }`
  - i2i: FLUX.2 / Kontext models → `{ prompt, input_image: <dataURL> }`;
    other models → `{ prompt, image: <dataURL>, prompt_strength }`
  - enhance: `{ image: <dataURL> }` (+ `scale` for real-esrgan)
  - i2v: kling → `{ start_image: <dataURL>, prompt }`; wan → `{ image: <dataURL>, prompt }`

### fal.ai
- POST `https://queue.fal.run/{model}` with body = the input object directly.
- If response has `status_url`, poll it until `status == "COMPLETED"`, then GET
  `response_url`. Output: images in `result.images[0].url`; video in
  `result.video.url` (or `videos[0].url`).
- Inputs:
  - t2i: `{ prompt, image_size? }`
  - i2i: `{ prompt, image_url: <dataURL>, strength }`
  - enhance: `{ image_url: <dataURL>, upscale_factor }`
  - i2v: `{ image_url: <dataURL>, prompt }`

### Black Forest Labs (FLUX.2) — same endpoint generates AND edits
- POST `https://api.bfl.ai/v1/{model}` with header `x-key`. Body:
  - t2i: `{ prompt, aspect_ratio? }`
  - i2i (edit): `{ prompt, input_image: <raw base64, NO data: prefix> }`
    (multi-reference also supports `input_image_2`, … up to 8)
- Response returns `polling_url`. Poll it (GET, same `x-key`) until
  `status == "Ready"`, then the image URL is `result.sample`. Treat
  `Error|Failed|Content Moderated|Request Moderated` as failure.
- Note: signed result URLs expire after ~10 minutes.

### OpenAI (text→image only on mobile)
- POST `https://api.openai.com/v1/images/generations`
  body `{ model:"gpt-image-1", prompt, size:"1024x1024" }`.
- Result in `data[0].b64_json` (→ data URL) or `data[0].url`.
- Skip image editing on mobile (needs multipart upload, awkward via native HTTP).

### xAI / Grok (text→image only)
- POST `https://api.x.ai/v1/images/generations`
  body `{ model:"grok-2-image", prompt, response_format:"url" }`.
- Result in `data[0].url` (or `b64_json`).

### Dispatch
`runTask(task, provider, model, key, {prompt,image,opts})` → look up
`ADAPTERS[provider][task]`; throw a clear error if the key is missing or the
provider doesn't support the task.

## 7. Build the APK
Prereqs: Node, JDK 17, Android SDK (via Android Studio).
```bash
npm install
npx cap add android      # first time only
npx cap sync android
cd android && ./gradlew assembleDebug
# → android/app/build/outputs/apk/debug/app-debug.apk
```
Optional cloud build: a GitHub Actions workflow (`.github/workflows/build-apk.yml`)
that sets up Node 20 + JDK 17 + Android SDK, runs the steps above, and uploads
`app-debug.apk` as an artifact — no local toolchain needed.

## 8. Acceptance criteria
- App installs and opens on Android; four tabs work.
- Keys persist across restarts; calls go directly to providers (no server).
- FLUX.2 models appear under both Text→Image and Image→Image.
- A successful t2i with a FLUX.2 (BFL) key renders an image and Save stores it.
- Errors (bad key, moderation, timeout) show a readable message, no crash.
```
