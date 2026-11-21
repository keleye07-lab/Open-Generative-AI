"""Render one shot of blocky 3D avatars with Blender (free, runs on CPU).

Run:  blender -b -P blender_shot.py -- shot.json
(headless Linux without GPU: wrap in `xvfb-run -a` so EEVEE gets a software OpenGL context)

shot.json:
{ "out_dir": ".../frames", "fps": 24, "frames": 72, "res": [720, 1280], "samples": 8,
  "set": "plaza" | "plane", "anim": "idle|talk|jump|awkward|run|shock|sit", "camera": "wide|close",
  "speaker": "daan" | null, "mouth": [0..1 per frame],
  "characters": [{"id": "daan", "shirt": "#e33", "pants": "#235", "skin": "#f2c27a",
                  "hair": "#3a2a1a" | null, "hat": "#c00" | null, "glasses": false, "prop": "phone" | null}] }
"""
import bpy, json, math, sys, random
from mathutils import Vector

cfg = json.load(open(sys.argv[sys.argv.index("--") + 1:][0]))
bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
FPS, NF = cfg.get("fps", 24), cfg["frames"]


def hexc(h):
    h = h.lstrip("#")
    if len(h) == 3: h = "".join(c * 2 for c in h)
    srgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb)


_mats = {}
def mat(col, rough=0.45, emit=0.0):
    key = (col, rough, emit)
    if key in _mats: return _mats[key]
    m = bpy.data.materials.new("m"); m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    c = hexc(col) if isinstance(col, str) else col
    b.inputs["Base Color"].default_value = (*c, 1); b.inputs["Roughness"].default_value = rough
    if emit:
        b.inputs["Emission Color"].default_value = (*c, 1); b.inputs["Emission Strength"].default_value = emit
    _mats[key] = m
    return m


def block(size, loc, m, parent=None, bevel=0.05, name="b"):
    """Box with its pivot at `loc`; geometry offset is baked so pivots work for limbs."""
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    o = bpy.context.object; o.name = name
    o.scale = (size[0] / 2, size[1] / 2, size[2] / 2); bpy.ops.object.transform_apply(scale=True)
    if bevel:
        bv = o.modifiers.new("bv", "BEVEL"); bv.width = bevel; bv.segments = 3
    o.data.materials.append(m)
    o.location = loc
    if parent: o.parent = parent
    return o


def pivot(loc, parent=None, name="p"):
    e = bpy.data.objects.new(name, None); sc.collection.objects.link(e)
    e.location = loc
    if parent: e.parent = parent
    return e


# ------------------------------------------------------------------ avatar rig
class Avatar:
    def __init__(self, c, x, y=0.0, face_yaw=0.0):
        self.id = c["id"]; self.base = Vector((x, y, 0)); self.yaw = face_yaw
        skin = mat(c.get("skin", "#f2c27a")); shirt = mat(c["shirt"]); pants = mat(c["pants"])
        dark = mat("#151515", 0.6)
        self.root = pivot(self.base, name=self.id)
        self.root.rotation_euler.z = face_yaw
        self.hips = pivot((0, 0, 2.0), self.root)
        self.legL = pivot((-0.25, 0, 0), self.hips); block((0.48, 0.5, 2.0), (0, 0, -1.0), pants, self.legL)
        self.legR = pivot((0.25, 0, 0), self.hips); block((0.48, 0.5, 2.0), (0, 0, -1.0), pants, self.legR)
        self.torso = pivot((0, 0, 0), self.hips); block((1.0, 0.5, 2.0), (0, 0, 1.0), shirt, self.torso)
        arm_m = mat(c["sleeves"]) if c.get("sleeves") else shirt
        self.armL = pivot((-0.75, 0, 1.85), self.torso); block((0.48, 0.5, 2.0), (0, 0, -0.9), arm_m, self.armL)
        self.armR = pivot((0.75, 0, 1.85), self.torso); block((0.48, 0.5, 2.0), (0, 0, -0.9), arm_m, self.armR)
        block((0.46, 0.48, 0.45), (0, 0, -1.75), skin, self.armL); block((0.46, 0.48, 0.45), (0, 0, -1.75), skin, self.armR)
        self.head = pivot((0, 0, 2.05), self.torso)
        block((1.2, 1.2, 1.2), (0, 0, 0.6), skin, self.head, bevel=0.12)
        self.eyes = [block((0.14, 0.04, 0.24), (sx, -0.6, 0.75), dark, self.head, 0.02) for sx in (-0.25, 0.25)]
        self.mouth = block((0.42, 0.04, 0.1), (0, -0.6, 0.38), mat("#3a0d12", 0.6), self.head, 0.02)
        if c.get("hair"):
            hm = mat(c["hair"]); block((1.3, 1.3, 0.35), (0, 0.02, 1.25), hm, self.head, 0.08)
            block((1.3, 0.3, 0.9), (0, 0.55, 0.85), hm, self.head, 0.08)
        if c.get("hat"):
            hm = mat(c["hat"]); block((1.3, 1.3, 0.45), (0, 0, 1.38), hm, self.head, 0.08)
            block((1.1, 0.6, 0.1), (0, -0.85, 1.2), hm, self.head, 0.03)
        if c.get("glasses"):
            block((1.05, 0.06, 0.28), (0, -0.63, 0.76), mat("#050505", 0.1), self.head, 0.03)
        self.ball = None
        if c.get("prop") == "ball":
            bpy.ops.mesh.primitive_uv_sphere_add(radius=0.42, segments=24, ring_count=12)
            self.ball = bpy.context.object; self.ball.data.materials.append(mat("#e8661c", 0.6))
            bpy.ops.object.shade_smooth()
            self.ball.parent = self.root
        if c.get("prop") == "phone":
            block((0.45, 0.08, 0.8), (0, -0.35, -1.9), mat("#111111", 0.2), self.armR, 0.03)
            block((0.38, 0.02, 0.68), (0, -0.4, -1.9), mat("#7fd3ff", 0.2, emit=1.5), self.armR, 0)

    def pose(self, t, anim, talking, mouth, phase):
        r = self.root; s = math.sin
        # reset
        for p in (self.legL, self.legR, self.armL, self.armR, self.head, self.torso):
            p.rotation_euler = (0, 0, 0)
        self.hips.location = (0, 0, 2.0); r.location = self.base.copy(); r.rotation_euler.z = self.yaw
        breathe = 0.03 * s(t * 2.2 + phase)
        self.torso.location.z = breathe
        self.armL.rotation_euler.x = 0.05 * s(t * 1.7 + phase); self.armR.rotation_euler.x = -0.05 * s(t * 1.7 + phase)
        if anim == "jump":
            hop = abs(s(t * 5.5 + phase)) * 1.3
            self.hips.location.z = 2.0 + hop
            self.armL.rotation_euler.y = -2.6; self.armR.rotation_euler.y = 2.6
            self.armL.rotation_euler.x = self.armR.rotation_euler.x = -0.3
            self.legL.rotation_euler.x = -0.4 * hop; self.legR.rotation_euler.x = 0.3 * hop
        elif anim == "run":
            w = t * 11 + phase
            self.legL.rotation_euler.x = 0.9 * s(w); self.legR.rotation_euler.x = -0.9 * s(w)
            self.armL.rotation_euler.x = -1.0 * s(w); self.armR.rotation_euler.x = 1.0 * s(w)
            self.torso.rotation_euler.x = -0.25
            self.hips.location.z = 2.0 + 0.25 * abs(s(w))
            r.location.y = self.base.y + 6 - t * 3.2
        elif anim == "awkward":
            turn = min(1.0, max(0.0, (t - 0.3) / 1.2))
            self.head.rotation_euler.z = (0.9 if phase % 2 < 1 else -0.9) * turn
            self.head.rotation_euler.x = -0.15 * turn
        elif anim == "sit":
            self.hips.location.z = 1.25
            self.legL.rotation_euler.x = self.legR.rotation_euler.x = -1.57
            self.armL.rotation_euler.x = self.armR.rotation_euler.x = -0.6
            self.torso.rotation_euler.x = 0.15 + 0.03 * s(t * 9)   # plane rumble
        if anim == "dribble":
            hits = cfg.get("beats") or [k * 0.45 for k in range(200)]
            prev = max([x for x in hits if x <= t], default=None)
            nxt = min([x for x in hits if x > t], default=None)
            top, low = 2.3, 0.42
            if prev is not None and nxt is not None and nxt - prev < 0.9:
                p = (t - prev) / (nxt - prev); hgt = low + (top - low) * s(math.pi * p) ** 0.8
            elif nxt is not None and nxt - t < 0.22:
                hgt = low + (top - low) * (nxt - t) / 0.22
            elif prev is not None and t - prev < 0.22:
                hgt = low + (top - low) * (t - prev) / 0.22
            else:
                hgt = top
            since = t - prev if prev is not None else 9
            self.hips.location.z = 2.0 - 0.18 * math.exp(-since * 12)
            self.torso.rotation_euler.x = -0.12
            reach = (top - hgt) / (top - low)
            self.armR.rotation_euler.x = -0.5 + 0.35 * reach
            if self.ball:
                self.ball.location = (1.05, -0.75, hgt)
                self.ball.scale = (1.0, 1.0, 1.0 - 0.18 * math.exp(-since * 30))
            self.head.rotation_euler.x = 0.15 * math.exp(-since * 8)
            mouth = max(mouth, 0.9 * math.exp(-since * 6)) if cfg.get("huss_mouth") else mouth
        elif self.ball:
            self.ball.location = (0.95, -0.5, 1.0)
        if talking:
            self.head.rotation_euler.x = 0.08 * s(t * 6) - 0.04
            self.head.rotation_euler.z += 0.1 * s(t * 2.3)
            if anim != "dribble":
                self.armR.rotation_euler.x = -1.1 + 0.25 * s(t * 4.5)
                self.armR.rotation_euler.y = 0.3
            if anim == "shock":
                k = min(1.0, t / 0.4)
                self.torso.rotation_euler.x = 0.18 * k
                self.armL.rotation_euler.x = -1.4 * k; self.armL.rotation_euler.y = -0.6 * k
                mouth = max(mouth, 0.9 * k)
                for e in self.eyes: e.scale = (1.3, 1, 1.0 + 0.5 * k)
        self.mouth.scale = (1.0 - 0.25 * mouth, 1, 1 + 4.5 * mouth)


# ------------------------------------------------------------------ sets
def build_plaza():
    ground = block((80, 80, 0.4), (0, 0, -0.2), mat("#5bb04a", 0.8), bevel=0)
    for gx in range(-10, 11, 2):                          # studs on the baseplate
        for gy in range(-4, 9, 2):
            bpy.ops.mesh.primitive_cylinder_add(radius=0.28, depth=0.12, location=(gx, gy, 0.05), vertices=16)
            bpy.context.object.data.materials.append(mat("#63bd52", 0.8))
    path = block((7, 80, 0.06), (0, 0, 0.03), mat("#c9c3b5", 0.9), bevel=0)
    rnd = random.Random(4)
    cols = ["#e8e2d4", "#e07a4f", "#5d8fd6", "#f2c94c", "#9b6fd6", "#d65d6f"]
    for i in range(-5, 6):
        if abs(i) < 1: continue
        hgt = rnd.choice([6, 9, 12, 15])
        x = i * 5.2
        b = block((4.6, 4.6, hgt), (x, 16 + rnd.uniform(-1, 2), hgt / 2), mat(rnd.choice(cols), 0.6), bevel=0.08)
        for wz in range(2, int(hgt) - 1, 3):
            for wx in (-1.2, 1.2):
                block((1.0, 0.1, 1.3), (x + wx, b.location.y - 2.31, wz), mat("#bfe3ff", 0.1, emit=0.2), bevel=0)
    for tx in (-9, 9, -14, 14):                            # blocky trees
        block((0.7, 0.7, 2.5), (tx, 6, 1.25), mat("#7a4e2c"))
        block((3, 3, 3), (tx, 6, 3.8), mat("#3f9a3a", 0.7), bevel=0.15)
    sky(top="#56a8ff", bottom="#cfe8ff")


def build_plane():
    block((14, 40, 0.3), (0, 0, -0.15), mat("#6b6f78", 0.8), bevel=0)
    block((0.4, 40, 7), (-6, 0, 3.5), mat("#eceff3", 0.5), bevel=0); block((0.4, 40, 7), (6, 0, 3.5), mat("#eceff3", 0.5), bevel=0)
    for y in range(-6, 20, 4):
        for wx in (-5.75, 5.75):
            block((0.1, 1.2, 1.6), (wx, y, 4.0), mat("#9fd8ff", 0.1, emit=1.0), bevel=0.05)
    for row in range(0, 4):
        for sx in (-3.2, -1.2, 1.2, 3.2):
            y = 1.5 + row * 3.2
            block((1.6, 1.4, 0.5), (sx, y, 1.0), mat("#2f4fa3"), bevel=0.08)
            block((1.6, 0.4, 2.6), (sx, y + 0.8, 2.3), mat("#2f4fa3"), bevel=0.08)
    sky(top="#9fd0ff", bottom="#ffffff", strength=0.8)


def build_night():
    build_plaza()
    sky(top="#0b1030", bottom="#3a2a6a", strength=0.35)
    sun.energy = 0.3
    rnd = random.Random(2)
    for k in range(14):
        col = rnd.choice(["#ff3cac", "#2ec5ff", "#ffe14d", "#9b5de5"])
        bpy.ops.object.light_add(type="POINT", location=(rnd.uniform(-14, 14), rnd.uniform(10, 15), rnd.uniform(3, 9)))
        L = bpy.context.object.data; L.energy = 250; L.color = hexc(col)
    for k in range(2):
        bpy.ops.object.light_add(type="POINT", location=(0, -3 + k * 4, 6)); bpy.context.object.data.energy = 150


def sky(top, bottom, strength=1.0):
    w = bpy.data.worlds.new("w"); sc.world = w; w.use_nodes = True
    nt = w.node_tree; bg = nt.nodes["Background"]
    tc = nt.nodes.new("ShaderNodeTexCoord"); sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*hexc(bottom), 1); ramp.color_ramp.elements[1].color = (*hexc(top), 1)
    nt.links.new(tc.outputs["Generated"], sep.inputs[0]); nt.links.new(sep.outputs["Z"], ramp.inputs[0])
    nt.links.new(ramp.outputs[0], bg.inputs[0]); bg.inputs[1].default_value = strength


# ------------------------------------------------------------------ scene
chars = cfg["characters"]
setname = cfg.get("set", "plaza")
sun = bpy.data.lights.new("sun", "SUN"); sun.energy = 3.2; sun.angle = 0.2
so = bpy.data.objects.new("sun", sun); sc.collection.objects.link(so)
so.rotation_euler = (math.radians(55), math.radians(10), math.radians(-35))
{"plane": build_plane, "night": build_night}.get(setname, build_plaza)()
avatars = []
n = len(chars)
for i, c in enumerate(chars):
    if setname == "plane":
        sx = [-3.2, -1.2, 1.2, 3.2][i % 4]; y = 1.5 + (i // 4) * 3.2
        avatars.append(Avatar(c, sx, y - 0.2))
    else:
        spacing = 2.3 if n > 1 else 0
        avatars.append(Avatar(c, (i - (n - 1) / 2) * spacing, 0 if n < 4 else (i % 2) * 0.8))
# crowd fillers for big group shots
if cfg.get("crowd"):
    rnd = random.Random(9)
    for k in range(cfg["crowd"]):
        col = rnd.choice(["#e33b3b", "#3b7be3", "#f0c419", "#7a3be3", "#3be38a", "#ff8a3b"])
        avatars.append(Avatar({"id": f"crowd{k}", "shirt": col, "pants": rnd.choice(["#223", "#444", "#235"]),
                               "hair": rnd.choice([None, "#2b1a10", "#d9b05c"])},
                              (k - cfg["crowd"] / 2) * 1.9 + rnd.uniform(-0.3, 0.3), 3.5 + rnd.uniform(0, 1.5)))


cam_d = bpy.data.cameras.new("cam"); cam = bpy.data.objects.new("cam", cam_d); sc.collection.objects.link(cam); sc.camera = cam
speaker = next((a for a in avatars if a.id == cfg.get("speaker")), None)


def place_camera(t):
    mode = cfg.get("camera", "wide")
    push = t / max(0.1, NF / FPS)
    if mode == "close" and speaker:
        bpy.context.view_layer.update()
        hp = speaker.head.matrix_world.translation
        cam_d.lens = 50
        cam.location = (hp.x + 0.6, hp.y - 7.5 + 1.0 * push, hp.z - 0.4)
        target = Vector((hp.x, hp.y, hp.z - 0.1))
    elif mode == "medium" and speaker:
        bp = speaker.root.matrix_world.translation
        cam_d.lens = 30
        cam.location = (bp.x + 1.5, bp.y - 10.5 + 1.2 * push, 2.6)
        target = Vector((bp.x + 0.5, bp.y, 2.4))
    elif setname == "plane":
        cam_d.lens = 22; cam.location = (0, -6 + 0.8 * push, 5.2); target = Vector((0, 4, 2.6))
    else:
        cam_d.lens = 24
        cam.location = (0, -21 + 2.0 * push, 5.0); target = Vector((0, 1.5, 3.2))
        if cfg.get("anim") == "run":
            cam.location = (0.0, -9.0, 3.0); target = Vector((0, 4, 3.0))
    d = target - Vector(cam.location)
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


# ------------------------------------------------------------------ render settings
rx, ry = cfg.get("res", [720, 1280])
sc.render.resolution_x, sc.render.resolution_y = rx, ry
sc.render.engine = "BLENDER_EEVEE"
sc.eevee.taa_render_samples = cfg.get("samples", 8)
sc.eevee.use_gtao = True; sc.eevee.gtao_distance = 1.2
sc.eevee.use_soft_shadows = True
sc.eevee.shadow_cascade_size = "2048"
sc.view_settings.view_transform = "Standard"; sc.view_settings.look = "Medium High Contrast"
sc.render.image_settings.file_format = "PNG"

mouth = cfg.get("mouth") or []
anim = cfg.get("anim", "idle")
for f in range(NF):
    t = f / FPS
    for i, a in enumerate(avatars):
        talking = speaker is a
        m = mouth[f] if (talking and f < len(mouth)) else 0.0
        if cfg.get("shout"):
            m = max(m, 0.5 + 0.4 * abs(math.sin(t * 7 + i)))
        a.pose(t, anim if not (anim == "shock" and not talking) else "idle", talking, m, i * 1.3)
    place_camera(t)
    sc.render.filepath = f"{cfg['out_dir']}/f_{f:04d}.png"
    bpy.ops.render.render(write_still=True)
print("SHOT_DONE", NF)
