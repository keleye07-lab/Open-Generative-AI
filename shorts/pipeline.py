#!/usr/bin/env python3
"""Episode JSON -> finished 9:16 short (mp4), using MuAPI models.

Steps (each cached in out/<episode>/state.json, so a rerun only redoes what changed):
  1. characters  text-to-image character sheet per character        (nano-banana-2)
  2. keyframes   per shot: scene image with the characters as refs  (nano-banana-2-edit)
  3. voices      per dialogue line: Dutch TTS                         (minimax-speech-2.6-hd)
  4. clips       talking shot -> lip-synced clip                      (infinitetalk-image-to-video)
                 other shots  -> animated clip                        (seedance-2-image-to-video-fast)
  5. assemble    crop to 1080x1920, captions, voices + sfx + beat, export mp4 (local ffmpeg)

Usage:
  python3 shorts/pipeline.py plan  shorts/episodes/porto.json
  python3 shorts/pipeline.py run   shorts/episodes/porto.json --mock        # free dry-run, placeholders
  MUAPI_KEY=... python3 shorts/pipeline.py run shorts/episodes/porto.json --yes
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, wave
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sfx
from muapi_client import Muapi, download

W, H, FPS = 1080, 1920, 30
SR = sfx.SR

DEFAULT_MODELS = {
    "character": {"endpoint": "nano-banana-2", "params": {"aspect_ratio": "9:16", "resolution": "1k"}},
    "keyframe": {"endpoint": "nano-banana-2-edit", "params": {"aspect_ratio": "9:16", "resolution": "1k"}},
    "tts": {"endpoint": "minimax-speech-2.6-hd",
            "params": {"language_boost": "Dutch", "format": "mp3", "sample_rate": 44100, "speed": 1}},
    "lipsync": {"endpoint": "infinitetalk-image-to-video", "params": {"resolution": "720p"}},
    "animate": {"endpoint": "seedance-2-image-to-video-fast", "params": {"aspect_ratio": "9:16", "duration": 5}},
}

FONT_CANDIDATES = ["/usr/share/fonts/opentype/inter/Inter-Black.otf",
                   "/System/Library/Fonts/Supplemental/Arial Black.ttf",
                   "C:/Windows/Fonts/ariblk.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]


def h(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:10]


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"command failed: {' '.join(cmd[:6])}...\n{r.stderr[-1500:]}")
    return r.stdout


def media_duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]).strip())


def read_audio(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def write_wav(path, x):
    x = np.clip(x, -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())


# ======================================================================= episode
class Episode:
    def __init__(self, path):
        self.path = path
        self.data = json.load(open(path, encoding="utf-8"))
        self.slug = self.data.get("slug") or os.path.splitext(os.path.basename(path))[0]
        self.style = self.data["style"]
        self.chars = self.data["characters"]
        self.shots = self.data["shots"]
        self.models = {k: {**v, "params": {**v["params"], **self.data.get("models", {}).get(k, {}).get("params", {})},
                           "endpoint": self.data.get("models", {}).get(k, {}).get("endpoint", v["endpoint"])}
                       for k, v in DEFAULT_MODELS.items()}

    def speakers(self, line):
        who = line["who"]
        if who == "all":
            return list(self.chars)
        return who if isinstance(who, list) else [who]

    def is_talking_shot(self, shot):
        lines = shot.get("lines", [])
        return (shot.get("lipsync", True) and len(lines) == 1 and len(self.speakers(lines[0])) == 1)


# ======================================================================= backends
class RealBackend:
    def __init__(self, ep, log):
        self.api = Muapi(log=log); self.ep = ep

    def upload(self, path):
        return self.api.upload(path)

    def gen(self, kind, payload, out_path):
        m = self.ep.models[kind]
        clean = {k: v for k, v in payload.items() if not k.startswith("_")}   # _keys are local-only
        url = self.api.run(m["endpoint"], {**m["params"], **clean})
        download(url, out_path)
        return url


class MockBackend:
    """Free offline stand-in: draws placeholder images, hums placeholder voices, Ken-Burns clips."""
    def __init__(self, ep, log):
        self.ep = ep; self.log = log

    def upload(self, path):
        return "file://" + os.path.abspath(path)

    def gen(self, kind, payload, out_path):
        self.log(f"  -> mock {kind}")
        getattr(self, "_" + kind)(payload, out_path)
        return "file://" + os.path.abspath(out_path)

    def _avatar(self, d, cx, base_y, scale, seed, name):
        r = np.random.default_rng(int(hashlib.md5(name.encode()).hexdigest()[:6], 16))
        shirt = tuple(int(v) for v in r.integers(60, 255, 3)); pants = tuple(int(v) for v in r.integers(20, 120, 3))
        s = scale
        d.rectangle([cx - 60 * s, base_y - 200 * s, cx - 5 * s, base_y], fill=pants, outline="black", width=3)
        d.rectangle([cx + 5 * s, base_y - 200 * s, cx + 60 * s, base_y], fill=pants, outline="black", width=3)
        d.rectangle([cx - 70 * s, base_y - 400 * s, cx + 70 * s, base_y - 200 * s], fill=shirt, outline="black", width=3)
        d.rectangle([cx - 130 * s, base_y - 400 * s, cx - 72 * s, base_y - 230 * s], fill=shirt, outline="black", width=3)
        d.rectangle([cx + 72 * s, base_y - 400 * s, cx + 130 * s, base_y - 230 * s], fill=shirt, outline="black", width=3)
        d.rectangle([cx - 65 * s, base_y - 530 * s, cx + 65 * s, base_y - 405 * s], fill=(240, 200, 150), outline="black", width=3)
        d.rectangle([cx - 35 * s, base_y - 480 * s, cx - 20 * s, base_y - 465 * s], fill="black")
        d.rectangle([cx + 20 * s, base_y - 480 * s, cx + 35 * s, base_y - 465 * s], fill="black")
        f = ImageFont.truetype(find_font(), int(36 * s))
        d.text((cx, base_y + 30 * s), name, font=f, fill="white", anchor="mm", stroke_width=3, stroke_fill="black")

    def _character(self, p, out):
        img = Image.new("RGB", (768, 1365), (90, 160, 230)); d = ImageDraw.Draw(img)
        self._avatar(d, 384, 1100, 1.6, 0, p["_name"]); img.save(out)

    def _keyframe(self, p, out):
        img = Image.new("RGB", (768, 1365)); d = ImageDraw.Draw(img)
        for y in range(1365):
            d.line([(0, y), (768, y)], fill=(80 + y // 20, 140, 220 - y // 12))
        d.rectangle([0, 1000, 768, 1365], fill=(110, 110, 120))
        names = p["_names"] or ["?"]
        n = len(names)
        for i, nm in enumerate(names):
            self._avatar(d, 768 * (i + 1) / (n + 1), 1050, 1.0 if n < 4 else 0.6, i, nm)
        f = ImageFont.truetype(find_font(), 28)
        d.multiline_text((30, 40), "\n".join(re.findall(".{1,40}(?:\\s|$)", p["_scene"]))[:300], font=f, fill="white")
        img.save(out)

    def _tts(self, p, out):
        secs = max(0.8, len(p["prompt"]) * 0.065)
        pitch = 110 + int(hashlib.md5(p["voice_id"].encode()).hexdigest()[:2], 16) % 120
        write_wav(out + ".wav", sfx.babble(secs, seed=len(p["prompt"]), pitch=pitch))
        run(["ffmpeg", "-y", "-v", "error", "-i", out + ".wav", out]); os.remove(out + ".wav")

    def _clip(self, image, seconds, out):
        frames = int(seconds * FPS)
        run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", image, "-vf",
             f"scale=1080:1920,zoompan=z='1+0.0015*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps={FPS}",
             "-t", str(seconds), "-pix_fmt", "yuv420p", out])

    def _lipsync(self, p, out):
        self._clip(p["_image_path"], media_duration(p["_audio_path"]) + 0.2, out)

    def _animate(self, p, out):
        self._clip(p["_image_path"], 5, out)


# ======================================================================= pipeline
class Pipeline:
    def __init__(self, ep, mock=False, outdir="out", log=print):
        self.ep, self.log, self.mock = ep, log, mock
        self.dir = os.path.join(outdir, ep.slug + ("-mock" if mock else ""))
        os.makedirs(self.dir, exist_ok=True)
        self.state_path = os.path.join(self.dir, "state.json")
        self.state = json.load(open(self.state_path)) if os.path.exists(self.state_path) else {}
        self.backend = (MockBackend if mock else RealBackend)(ep, log)

    def save(self):
        json.dump(self.state, open(self.state_path, "w"), indent=2)

    def cached(self, key, kind, payload, filename, extra=None):
        """Generate once per (inputs hash); returns local file path."""
        sig = h(kind, {k: v for k, v in payload.items() if not k.endswith("_url") and not k.startswith("_")}, extra, self.ep.models[kind])
        ent = self.state.get(key)
        path = os.path.join(self.dir, filename)
        if ent and ent.get("sig") == sig and os.path.exists(ent["file"]):
            return ent
        url = self.backend.gen(kind, payload, path)
        self.state[key] = {"sig": sig, "file": path, "url": url}
        self.save()
        return self.state[key]

    def remote_url(self, ent):
        """URL MuAPI can read for a local/generated file (generated outputs are already hosted)."""
        if ent.get("url", "").startswith("http") or self.mock:
            return ent["url"]
        ent["url"] = self.backend.upload(ent["file"]); self.save()
        return ent["url"]

    # ------------------------------------------------------------- steps
    def characters(self):
        refs = {}
        for cid, c in self.ep.chars.items():
            prompt = (f"{self.ep.style}. Character reference sheet, single character, full body, front view, "
                      f"neutral pose, plain light background. {c['look']}")
            refs[cid] = self.cached(f"char:{cid}", "character", {"prompt": prompt, "_name": c["name"]},
                                    f"char_{cid}.png")
        return refs

    def keyframes(self, refs):
        out = {}
        for s in self.ep.shots:
            cids = s.get("chars", [])
            prompt = (f"{self.ep.style}. Vertical 9:16 frame. {s['scene']} "
                      + " ".join(f"{self.ep.chars[c]['name']} looks exactly like reference image {i + 1}."
                                 for i, c in enumerate(cids[:6])))
            images = [self.remote_url(refs[c]) for c in cids[:6]]
            if not images:                       # crowd/empty scene -> plain text-to-image
                ent = self.cached(f"key:{s['id']}", "character",
                                  {"prompt": prompt, "_name": s["id"]}, f"key_{s['id']}.png")
            else:
                ent = self.cached(f"key:{s['id']}", "keyframe",
                                  {"prompt": prompt, "images_list": images,
                                   "_names": [self.ep.chars[c]["name"] for c in cids], "_scene": s["scene"]},
                                  f"key_{s['id']}.png")
            out[s["id"]] = ent
        return out

    def voices(self):
        out = {}
        for s in self.ep.shots:
            for li, line in enumerate(s.get("lines", [])):
                for cid in self.ep.speakers(line):
                    c = self.ep.chars[cid]
                    payload = {"prompt": line["text"], "voice_id": c["voice"],
                               "emotion": line.get("emotion", "neutral")}
                    if "pitch" in c: payload["pitch"] = c["pitch"]
                    if "speed" in line: payload["speed"] = line["speed"]
                    out[(s["id"], li, cid)] = self.cached(f"tts:{s['id']}:{li}:{cid}", "tts", payload,
                                                          f"vo_{s['id']}_{li}_{cid}.mp3")
        return out

    def clips(self, keys, voices):
        out = {}
        for s in self.ep.shots:
            k = keys[s["id"]]
            if self.ep.is_talking_shot(s):
                line = s["lines"][0]; cid = self.ep.speakers(line)[0]
                v = voices[(s["id"], 0, cid)]
                payload = {"image_url": self.remote_url(k), "audio_url": self.remote_url(v),
                           "prompt": s.get("motion", "the character talks expressively to the camera"),
                           "_image_path": k["file"], "_audio_path": v["file"]}
                out[s["id"]] = self.cached(f"clip:{s['id']}", "lipsync", payload, f"clip_{s['id']}.mp4",
                                           extra=[k.get("sig"), v.get("sig")])
            else:
                payload = {"prompt": f"{s.get('motion', '')} {self.ep.style}. Smooth game-style animation.",
                           "images_list": [self.remote_url(k)], "_image_path": k["file"]}
                out[s["id"]] = self.cached(f"clip:{s['id']}", "animate", payload, f"clip_{s['id']}.mp4",
                                           extra=[k.get("sig")])
        return out

    # ------------------------------------------------------------- assemble
    def layout(self, s, voices):
        """Shot length + where each voice file starts: [(offset, file, gain, speaker)]."""
        lead = 0.0 if self.ep.is_talking_shot(s) else 0.15
        cursor, vo = lead, []
        for li, line in enumerate(s.get("lines", [])):
            sp = self.ep.speakers(line)
            durs = [media_duration(voices[(s["id"], li, c)]["file"]) for c in sp]
            for c in sp:
                vo.append((cursor, voices[(s["id"], li, c)]["file"], 1.0 / max(1, len(sp)) ** 0.5, c))
            cursor += max(durs) + line.get("pause", 0.15)
        return max(s.get("duration", 2.5), cursor + 0.1), vo

    def assemble(self, clips, voices):
        tmp = os.path.join(self.dir, "assemble"); shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
        font = find_font()
        parts, t = [], 0.0
        timeline = []          # (start, kind, data)
        for s in self.ep.shots:
            dur, vo = self.layout(s, voices)
            # video part
            cap = os.path.join(tmp, f"cap_{s['id']}.png")
            draw_caption(s.get("caption", ""), cap, font)
            part = os.path.join(tmp, f"part_{s['id']}.mp4")
            run(["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", clips[s["id"]]["file"], "-i", cap,
                 "-filter_complex",
                 f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1[v];"
                 f"[v][1:v]overlay=0:0[o]",
                 "-map", "[o]", "-t", f"{dur:.3f}", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
                 "-pix_fmt", "yuv420p", part])
            parts.append(part)
            for off, f, g, _ in vo:
                timeline.append((t + off, "vo", (f, g)))
            if s.get("sfx"):
                timeline.append((t, "sfx", s["sfx"]))
            s["_start"], s["_dur"] = t, dur
            t += dur
        total = t
        # audio mix
        mix = np.zeros(int(total * SR) + SR)
        voice_mask = np.zeros_like(mix)
        def put(sig, at, g=1.0):
            i = int(at * SR); j = min(len(mix), i + len(sig)); mix[i:j] += sig[: j - i] * g; return i, j
        for at, kind, data in timeline:
            if kind == "vo":
                i, j = put(read_audio(data[0]), at, data[1]); voice_mask[i:j] = 1
            else:
                for name in ([data] if isinstance(data, str) else data):
                    put(sfx.SFX[name](), at, 0.8)
        music = self.ep.data.get("music")
        if music:
            start_shot = music.get("from_shot")
            m0 = next((s["_start"] for s in self.ep.shots if s["id"] == start_shot), 0.0)
            bed = sfx.beat(total - m0, music.get("bpm", 150))
            duck = np.convolve(voice_mask, np.ones(4410) / 4410, "same")[int(m0 * SR):][: len(bed)]
            bed = bed[: len(duck)] * (music.get("volume", 0.5) * (1 - 0.6 * np.clip(duck, 0, 1)))
            put(bed, m0)
        mix = mix[: int(total * SR)]
        mix = np.tanh(mix * 1.1); mix /= max(1e-9, np.abs(mix).max()); mix *= 0.95
        wav = os.path.join(tmp, "mix.wav"); write_wav(wav, mix)
        lst = os.path.join(tmp, "parts.txt")
        open(lst, "w").write("".join(f"file '{os.path.abspath(p)}'\n" for p in parts))
        out = os.path.join(self.dir, f"{self.ep.slug}{'-mock' if self.mock else ''}.mp4")
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-i", wav,
             "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-shortest", "-movflags", "+faststart", out])
        return out, total

    def run_all(self):
        self.log("1/5 characters"); refs = self.characters()
        self.log("2/5 keyframes"); keys = self.keyframes(refs)
        self.log("3/5 voices"); voices = self.voices()
        self.log("4/5 clips"); clips = self.clips(keys, voices)
        self.log("5/5 assemble"); out, total = self.assemble(clips, voices)
        self.log(f"done: {out} ({total:.1f}s)")
        return out


# ======================================================================= captions
def find_font():
    for f in FONT_CANDIDATES:
        if os.path.exists(f):
            return f
    raise RuntimeError("no bold font found; add one to FONT_CANDIDATES")


def draw_caption(text, out, font_path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    text = "".join(ch for ch in text if ord(ch) < 0x2600).strip()   # bold fonts have no emoji glyphs
    if text:
        d = ImageDraw.Draw(img)
        size = 110
        f = ImageFont.truetype(font_path, size)
        while max(d.textlength(l, font=f) for l in text.split("\n")) > W - 100 and size > 40:
            size -= 6; f = ImageFont.truetype(font_path, size)
        d.multiline_text((W / 2, 380), text, font=f, fill="white", anchor="mm", align="center",
                         spacing=12, stroke_width=max(5, size // 9), stroke_fill="black")
    img.save(out)


# ======================================================================= cli
def plan(ep):
    talking = sum(ep.is_talking_shot(s) for s in ep.shots)
    tts = sum(len(ep.speakers(l)) for s in ep.shots for l in s.get("lines", []))
    print(f"Episode '{ep.slug}': {len(ep.shots)} shots, {len(ep.chars)} characters")
    print(f"  {len(ep.chars)} character images ({ep.models['character']['endpoint']})")
    print(f"  {len(ep.shots)} keyframes ({ep.models['keyframe']['endpoint']})")
    print(f"  {tts} voice lines ({ep.models['tts']['endpoint']})")
    print(f"  {talking} lip-sync clips ({ep.models['lipsync']['endpoint']})")
    print(f"  {len(ep.shots) - talking} animated clips ({ep.models['animate']['endpoint']})")
    print("Cached steps are skipped on reruns; check muapi.ai pricing for the per-call cost.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "run"])
    ap.add_argument("episode")
    ap.add_argument("--mock", action="store_true", help="offline placeholders, no MuAPI calls, free")
    ap.add_argument("--yes", action="store_true", help="confirm paid MuAPI generations")
    ap.add_argument("--free", action="store_true", help="free offline render: Blender avatars + local TTS")
    ap.add_argument("--res", default="720x1280", help="--free render size (upscaled to 1080x1920)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
    a = ap.parse_args()
    ep = Episode(a.episode)
    if a.cmd == "plan":
        plan(ep); return
    if a.free:
        from free.local import FreePipeline
        rx, ry = (int(v) for v in a.res.split("x"))
        FreePipeline(ep, outdir=a.out, res=(rx, ry)).run_all(); return
    if not a.mock and not a.yes:
        plan(ep); print("\nThis uses paid MuAPI credits. Re-run with --yes to go ahead."); return
    Pipeline(ep, mock=a.mock, outdir=a.out).run_all()


if __name__ == "__main__":
    main()
