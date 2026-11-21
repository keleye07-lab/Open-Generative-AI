# Shorts pipeline: idea → finished 9:16 video

Turns an episode file (`episodes/*.json`) into a finished YouTube Short / Instagram Reel:
blocky 3D game-style characters, natural Dutch voices, lip-sync, captions, sfx and a beat.
It uses the same MuAPI models as the studio app in this repo.

| Step | What | Model (MuAPI) |
|------|------|---------------|
| 1 | Character sheet per character | `nano-banana-2` |
| 2 | Keyframe per shot, characters kept consistent via reference images | `nano-banana-2-edit` |
| 3 | Voice per dialogue line (Dutch) | `minimax-speech-2.6-hd` |
| 4 | Talking shot → lip-synced clip, other shots → animated clip | `infinitetalk-image-to-video`, `seedance-2-image-to-video-fast` |
| 5 | Crop to 1080×1920, captions, voices + sfx + beat → mp4 | local ffmpeg |

Every generation is cached in `out/<episode>/state.json`. If you change one line of dialogue,
only that voice and its clip are regenerated, so you don't pay twice.

## Use

```bash
pip install -r shorts/requirements.txt      # plus ffmpeg on PATH
python3 shorts/pipeline.py plan shorts/episodes/porto.json          # what will be generated
python3 shorts/pipeline.py run  shorts/episodes/porto.json --mock   # free dry-run with placeholders
MUAPI_KEY=xxxx python3 shorts/pipeline.py run shorts/episodes/porto.json --yes   # the real thing
```

Output: `shorts/out/porto/porto.mp4`.

## Writing an episode

See `episodes/porto.json`. Per shot: `chars` (who is in frame), `scene` (image prompt),
`motion` (animation prompt), `lines` (`who` = a character id, a list, or `"all"`), `caption`,
optional `sfx` (`crickets`, `whoosh`, `boom`, `scratch`), `duration` and `lipsync: false`.
A shot with exactly one speaker is lip-synced automatically. Swap models per episode with
`"models": {"animate": {"endpoint": "kling-v3.0-omni-standard-image-to-video", "params": {...}}}`.

The look is "Roblox-like" (blocky avatars), not real Roblox assets: no logos or official
characters, which keeps the videos safe from copyright claims.

## Free mode (no API key): `--free`

```bash
python3 shorts/pipeline.py run shorts/episodes/porto.json --free
```

Renders the same episode fully offline:

| Part | Free tool | Notes |
|------|-----------|-------|
| Characters + animation | **Blender** (EEVEE) via `free/blender_shot.py` | Blocky 3D avatars built from the `blocky` look in the episode; presets `talk`, `jump`, `awkward`, `run`, `shock`, `sit`; sets `plaza`, `plane`. |
| Lip sync | mouth driven by the voice's loudness per frame | Fits blocky painted faces better than Wav2Lip/SadTalker, which are made for real faces. |
| Voices | **Piper** (4 Dutch voices: `nl-rdh-medium` male, `nl-mls_7432-low`, `nl-mls_5809-low`, `nl-nathalie-x-low` female), else **espeak-ng** | Voices auto-download from the Piper GitHub release on first run. Each character gets its own voice (+ pitch shift), boys/girls guessed from `look`; override per character with `"free_voice": {"piper": "nl-rdh-medium", "semitones": -3, "tempo": 1.0}` or `"gender": "f"`. |
| Render | ffmpeg | same captions/sfx/beat as the paid path |

Per shot, optional `"free": {"anim": "...", "camera": "wide|close", "set": "plaza|plane", "crowd": 7}`.

Speed: ~3–6 s per frame at 720×1280 on a 4-core CPU without GPU (a 35 s short takes about an hour).
With an NVIDIA/AMD GPU, Blender renders the same in a few minutes. Install: Blender ≥ 4.0, ffmpeg,
`pip install piper-tts numpy Pillow`, ffmpeg with `rubberband`; on a headless Linux server also `xvfb`.
