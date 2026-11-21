"""Free, offline backend: Blender (blocky 3D avatars) + local Dutch TTS (Piper, else espeak-ng).

No API key, no credits. Runs on CPU; a GPU only makes Blender faster.
"""
import hashlib, json, math, os, shutil, subprocess, sys, urllib.request, wave
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pipeline import Pipeline, run, read_audio, media_duration, h, SR  # noqa: E402

FPS = 24
VOICE_DIR = os.path.join(HERE, "voices")
# multi-speaker Dutch Piper voice: one model, many distinct speakers (good for a cast)
PIPER_MODEL = "nl_NL-mls-medium"
PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/nl/nl_NL/mls/medium/"
PALETTE = ["#e63946", "#457b9d", "#2a9d8f", "#f4a261", "#9b5de5", "#f15bb5", "#00bbf9", "#fee440"]


def _seed(s):
    return int(hashlib.md5(s.encode()).hexdigest()[:8], 16)


# ======================================================================= TTS
class LocalTTS:
    def __init__(self, log=print):
        self.log = log
        self.piper = None
        model = os.path.join(VOICE_DIR, PIPER_MODEL + ".onnx")
        if not os.path.exists(model):
            self._try_download(model)
        if os.path.exists(model):
            try:
                from piper import PiperVoice
                self.piper = PiperVoice.load(model)
                self.n_speakers = getattr(self.piper.config, "num_speakers", 1) or 1
                log(f"  tts: Piper ({PIPER_MODEL}, {self.n_speakers} speakers)")
            except Exception as e:      # pragma: no cover
                log(f"  tts: Piper failed to load ({e}); falling back to espeak-ng")
        if not self.piper:
            if not shutil.which("espeak-ng"):
                raise RuntimeError("No local TTS: install piper voices (see README) or espeak-ng")
            log("  tts: espeak-ng (robotic). Allow huggingface.co once to get natural Piper voices.")

    def _try_download(self, model):
        os.makedirs(VOICE_DIR, exist_ok=True)
        try:
            for ext in (".onnx", ".onnx.json"):
                urllib.request.urlretrieve(PIPER_URL + PIPER_MODEL + ext, model.replace(".onnx", ext))
            self.log("  tts: downloaded Piper Dutch voice")
        except Exception:
            for ext in (".onnx", ".onnx.json"):
                p = model.replace(".onnx", ext)
                if os.path.exists(p): os.remove(p)

    def say(self, text, char_id, voice_cfg, emotion, out_wav):
        if self.piper:
            from piper import SynthesisConfig
            spk = voice_cfg.get("piper_speaker", _seed(char_id) % self.n_speakers)
            speed = {"surprised": 0.95, "angry": 0.85, "happy": 0.95, "fearful": 1.05, "sad": 1.15}.get(emotion, 1.0)
            cfg = SynthesisConfig(speaker_id=spk, length_scale=voice_cfg.get("length_scale", speed),
                                  noise_scale=0.75, noise_w_scale=0.9)
            with wave.open(out_wav, "wb") as w:
                self.piper.synthesize_wav(text, w, syn_config=cfg)
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
        engine = None
        for s in self.ep.shots:
            for li, line in enumerate(s.get("lines", [])):
                for cid in self.ep.speakers(line):
                    vc = self.ep.chars[cid].get("free_voice", {})
                    key = f"ftts:{s['id']}:{li}:{cid}"
                    sig = h(line["text"], line.get("emotion"), vc, os.path.exists(os.path.join(VOICE_DIR, PIPER_MODEL + ".onnx")))
                    path = os.path.join(self.dir, f"vo_{s['id']}_{li}_{cid}.wav")
                    ent = self.state.get(key)
                    if not (ent and ent["sig"] == sig and os.path.exists(path)):
                        self.tts().say(line["text"], cid, vc, line.get("emotion", "neutral"), path)
                        self.state[key] = {"sig": sig, "file": path, "url": ""}; self.save()
                    out[(s["id"], li, cid)] = self.state[key]
        return out

    def mouth_curve(self, vo, speaker, frames):
        curve = np.zeros(frames)
        for off, f, g, who in vo:
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
