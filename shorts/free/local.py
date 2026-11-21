"""Free, offline backend: Blender (blocky 3D avatars) + local Dutch TTS (Piper, else espeak-ng).

No API key, no credits. Runs on CPU; a GPU only makes Blender faster.
"""
import hashlib, json, math, os, shutil, subprocess, sys, tarfile, urllib.request, wave
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pipeline import Pipeline, run, read_audio, write_wav, media_duration, h, SR  # noqa: E402

FPS = 24
VOICE_DIR = os.path.join(HERE, "voices")
# Dutch Piper voices. huggingface.co serves the big .onnx files from a CDN that many networks block,
# so we use the same voices from the Piper GitHub release (v0.0.2).
PIPER_REL = "https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-{}.tar.gz"
PIPER_VOICES = {            # name: gender (measured pitch: rdh ~107 Hz, the others 165-230 Hz)
    "nl-rdh-medium": "m", "nl-mls_7432-low": "f", "nl-mls_5809-low": "f", "nl-nathalie-x-low": "f",
}
# cast slots: (voice, pitch shift in semitones, tempo). Every character in an episode gets its own slot.
CAST = {
    "m": [("nl-rdh-medium", 0, 1.0), ("nl-rdh-medium", -3, 1.0), ("nl-rdh-medium", 3, 1.05),
          ("nl-rdh-medium", -1.5, 0.92), ("nl-rdh-medium", 5, 1.1)],
    "f": [("nl-mls_7432-low", 0, 1.0), ("nl-mls_5809-low", 0, 1.0), ("nl-nathalie-x-low", 2, 1.0),
          ("nl-mls_7432-low", 3, 1.05), ("nl-mls_5809-low", -2, 0.95)],
}
PALETTE = ["#e63946", "#457b9d", "#2a9d8f", "#f4a261", "#9b5de5", "#f15bb5", "#00bbf9", "#fee440"]


def _seed(s):
    return int(hashlib.md5(s.encode()).hexdigest()[:8], 16)


def gender(c):
    if c.get("gender") in ("m", "f"): return c["gender"]
    t = (c.get("look", "") + " " + c.get("voice", "")).lower()
    return "f" if any(w in t for w in ("girl", "woman", "female", "lady", "meisje", "vrouw")) else "m"


def cast(chars):
    """Character id -> (voice, semitones, tempo); distinct per character, in episode order."""
    out, used = {}, {"m": 0, "f": 0}
    for cid, c in chars.items():
        fv = c.get("free_voice", {})
        g = gender(c); slot = CAST[g][used[g] % len(CAST[g])]; used[g] += 1
        out[cid] = (fv.get("piper", slot[0]), fv.get("semitones", slot[1]), fv.get("tempo", slot[2]))
    return out


# ======================================================================= TTS
class LocalTTS:
    def __init__(self, log=print):
        self.log = log
        self.voices = {}
        os.makedirs(VOICE_DIR, exist_ok=True)
        try:
            from piper import PiperVoice
        except ImportError:
            PiperVoice = None; log("  tts: pip install piper-tts for natural voices")
        for name in PIPER_VOICES if PiperVoice else []:
            model = os.path.join(VOICE_DIR, name + ".onnx")
            if not os.path.exists(model): self._download(name)
            if os.path.exists(model):
                try: self.voices[name] = PiperVoice.load(model)
                except Exception as e: log(f"  tts: {name} failed to load ({e})")
        if self.voices:
            log(f"  tts: Piper ({', '.join(self.voices)})")
        else:
            if not shutil.which("espeak-ng"):
                raise RuntimeError("No local TTS: install piper-tts (+ voices) or espeak-ng")
            log("  tts: espeak-ng (robotic). Allow github.com downloads to get natural Piper voices.")

    def _download(self, name):
        tgz = os.path.join(VOICE_DIR, name + ".tar.gz")
        try:
            urllib.request.urlretrieve(PIPER_REL.format(name), tgz)
            with tarfile.open(tgz) as t:
                for m in t.getmembers():
                    if m.name.endswith((".onnx", ".onnx.json")) and "/" not in m.name.strip("./"):
                        t.extract(m, VOICE_DIR)
            self.log(f"  tts: downloaded Piper voice {name}")
        except Exception as e:
            self.log(f"  tts: could not download {name} ({e})")
        finally:
            if os.path.exists(tgz): os.remove(tgz)

    def say(self, text, char_id, voice_cfg, emotion, out_wav, slot=None):
        name, semis, tempo = slot or (None, 0, 1.0)
        if self.voices:
            from piper import SynthesisConfig
            voice = self.voices.get(name) or self.voices[sorted(self.voices)[_seed(char_id) % len(self.voices)]]
            speed = {"surprised": 0.95, "angry": 0.88, "happy": 0.95, "fearful": 0.92, "sad": 1.12}.get(emotion, 1.0)
            cfg = SynthesisConfig(length_scale=voice_cfg.get("length_scale", speed / tempo),
                                  noise_scale=0.7, noise_w_scale=0.85)
            raw = out_wav + ".raw.wav"
            with wave.open(raw, "wb") as w:
                voice.synthesize_wav(text, w, syn_config=cfg)
            af = [f"rubberband=pitch={2 ** (semis / 12):.4f}"] if semis else []
            trim = "silenceremove=start_periods=1:start_threshold=-45dB"     # tight starts: captions/beat line up
            af += [trim, "areverse", trim, "areverse", "highpass=f=70", "loudnorm=I=-16:TP=-1.5"]
            run(["ffmpeg", "-y", "-v", "error", "-i", raw, "-af", ",".join(af), "-ar", "44100", out_wav])
            os.remove(raw)
        else:
            variant = voice_cfg.get("espeak", "nl+" + ["m1", "m3", "m7", "f2", "f4", "m2", "f3"][_seed(char_id) % 7])
            pitch = str(voice_cfg.get("pitch", 35 + _seed(char_id) % 40))
            speed = str(voice_cfg.get("speed", 165 if emotion != "angry" else 190))
            run(["espeak-ng", "-v", variant, "-p", pitch, "-s", speed, "-w", out_wav, text])
        return out_wav


# ======================================================================= pipeline
class FreePipeline(Pipeline):
    def __init__(self, ep, outdir, log=print, res=(720, 1280), samples=8):
        self.res, self.samples = list(res), samples
        super().__init__(ep, mock=True, outdir=outdir, log=log)    # mock=True: no MuAPI client
        self.dir = os.path.join(outdir, ep.slug + "-free"); os.makedirs(self.dir, exist_ok=True)
        self.state_path = os.path.join(self.dir, "state.json")
        self.state = json.load(open(self.state_path)) if os.path.exists(self.state_path) else {}
        self.mock = False
        self._tts = None

    def tts(self):
        if not self._tts: self._tts = LocalTTS(self.log)
        return self._tts

    # ------------------------------------------------------------- characters
    def look(self, cid):
        c = self.ep.chars[cid]; b = dict(c.get("blocky", {}))
        r = _seed(cid)
        b.setdefault("shirt", PALETTE[r % len(PALETTE)]); b.setdefault("pants", ["#1d3557", "#333333", "#5a3d2b"][r % 3])
        b.setdefault("skin", "#f2c27a"); b["id"] = cid
        return b

    # ------------------------------------------------------------- steps
    def voices(self):
        out = {}
        slots = cast(self.ep.chars)
        have_piper = bool(self.tts().voices)
        for s in self.ep.shots:
            for li, line in enumerate(s.get("lines", [])):
                for cid in self.ep.speakers(line):
                    vc = self.ep.chars[cid].get("free_voice", {})
                    key = f"ftts:{s['id']}:{li}:{cid}"
                    sig = h(line["text"], line.get("emotion"), line.get("speed"), vc, slots[cid], have_piper, self.beat(), "v3")
                    path = os.path.join(self.dir, f"vo_{s['id']}_{li}_{cid}.wav")
                    ent = self.state.get(key)
                    if not (ent and ent["sig"] == sig and os.path.exists(path)):
                        name, semis, tempo = slots[cid]
                        words = [w.strip(",.!?…").lower() for w in line["text"].split()]
                        if len(words) > 1 and all(w == "huss" for w in words):
                            self.chant(len(words), cid, vc, (name, semis, tempo), path)
                        else:
                            self.tts().say(line["text"], cid, vc, line.get("emotion", "neutral"), path,
                                           (name, semis, tempo * line.get("speed", 1.0)))
                        self.state[key] = {"sig": sig, "file": path, "url": ""}; self.save()
                    out[(s["id"], li, cid)] = self.state[key]
        return out

    def beat(self):
        return 60.0 / self.ep.data.get("music", {}).get("bpm", 150)

    def chant(self, n, cid, vc, slot, path):
        """'Huss huss huss ...': one shouted word repeated on the music beat (TTS rushes it otherwise)."""
        one = path + ".one.wav"
        step = int(self.beat() * SR)
        self.tts().say("Huss!", cid, vc, "angry", one, slot)
        a = read_audio(one)
        if len(a) > 0.9 * step:          # squeeze the word so it fits one beat
            self.tts().say("Huss!", cid, vc, "angry", one, (slot[0], slot[1], slot[2] * len(a) / (0.85 * step)))
            a = read_audio(one)
        os.remove(one)
        a = a[:step].copy(); fade = min(len(a), int(0.015 * SR)); a[-fade:] *= np.linspace(1, 0, fade)
        out = np.zeros(step * n)
        for k in range(n):
            out[k * step:k * step + len(a)] += a * (1.0 if k < n - 1 else 1.15)
        write_wav(path, out * 0.9)

    def mouth_curve(self, vo, speaker, frames):
        curve = np.zeros(frames)
        for off, f, g, who, *_ in vo:
            if who != speaker: continue
            a = read_audio(f)
            hop = SR // FPS
            for k in range(len(a) // hop):
                fi = int(off * FPS) + k
                if fi < frames:
                    curve[fi] = max(curve[fi], float(np.sqrt(np.mean(a[k * hop:(k + 1) * hop] ** 2))))
        if curve.max() > 0:
            curve = np.clip(curve / (np.percentile(curve[curve > 0], 90) + 1e-9), 0, 1)
        # smooth a bit so the mouth doesn't flicker
        return np.convolve(curve, [0.25, 0.5, 0.25], "same").round(3).tolist()

    def shot_cfg(self, s, voices):
        dur, vo = self.layout(s, voices)
        frames = int(math.ceil(dur * FPS)) + 2
        lines = s.get("lines", [])
        group = any(l["who"] == "all" or isinstance(l["who"], list) for l in lines)
        speaker = self.ep.speakers(lines[0])[0] if lines and not group else None
        fr = s.get("free", {})
        anim = fr.get("anim") or ("jump" if group else "talk" if speaker else "awkward")
        camera = fr.get("camera") or ("close" if speaker and len(s.get("chars", [])) <= 1 else "wide")
        return {
            "fps": FPS, "frames": frames, "res": self.res, "samples": self.samples,
            "set": fr.get("set", "plaza"), "anim": anim, "camera": camera, "crowd": fr.get("crowd", 0),
            "shout": bool(group), "speaker": speaker,
            "mouth": self.mouth_curve(vo, speaker, frames) if speaker else [],
            "beats": [a for a, b, w, hot in self.word_timings(s, vo) if "HUSS" in w.upper()] or fr.get("beats"),
            "huss_mouth": True,
            "characters": [self.look(c) for c in s.get("chars", [])],
        }

    def clips(self, keys, voices):
        out = {}
        blender = shutil.which("blender") or os.environ.get("BLENDER", "blender")
        for s in self.ep.shots:
            cfg = self.shot_cfg(s, voices)
            sig = h(cfg, open(os.path.join(HERE, "blender_shot.py")).read())
            key, path = f"fclip:{s['id']}", os.path.join(self.dir, f"clip_{s['id']}.mp4")
            ent = self.state.get(key)
            if not (ent and ent["sig"] == sig and os.path.exists(path)):
                frames_dir = os.path.join(self.dir, f"frames_{s['id']}")
                shutil.rmtree(frames_dir, ignore_errors=True); os.makedirs(frames_dir)
                cfg["out_dir"] = frames_dir
                cfg_path = os.path.join(self.dir, f"shot_{s['id']}.json"); json.dump(cfg, open(cfg_path, "w"))
                cmd = [blender, "-b", "-P", os.path.join(HERE, "blender_shot.py"), "--", cfg_path]
                if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
                    cmd = ["xvfb-run", "-a", "-s", "-screen 0 1280x1024x24"] + cmd
                self.log(f"  -> blender {s['id']} ({cfg['frames']} frames, {cfg['anim']}/{cfg['camera']})")
                r = subprocess.run(cmd, capture_output=True, text=True)
                if "SHOT_DONE" not in r.stdout:
                    raise RuntimeError(f"Blender failed on {s['id']}:\n{(r.stdout + r.stderr)[-2000:]}")
                run(["ffmpeg", "-y", "-v", "error", "-framerate", str(FPS), "-i", os.path.join(frames_dir, "f_%04d.png"),
                     "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", path])
                shutil.rmtree(frames_dir, ignore_errors=True)
                self.state[key] = {"sig": sig, "file": path, "url": ""}; self.save()
            out[s["id"]] = self.state[key]
        return out

    def run_all(self):
        self.log("1/3 voices (local)"); voices = self.voices()
        self.log("2/3 clips (Blender)"); clips = self.clips(None, voices)
        self.log("3/3 assemble"); out, total = self.assemble(clips, voices)
        final = out.replace("-mock", "")
        if final != out: os.replace(out, final)
        self.log(f"done: {final} ({total:.1f}s)")
        return final
