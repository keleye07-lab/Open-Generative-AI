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
