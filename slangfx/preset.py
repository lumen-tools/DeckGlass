"""Parser for RetroArch .slangp presets and .slang shader sources."""
import os
import re

TRUE_VALUES = ("true", "1", "yes")


class PresetError(Exception):
    pass


def resolve_ci(path):
    """Case-insensitive path lookup (presets are often authored on Windows)."""
    if os.path.exists(path):
        return path
    parts = os.path.normpath(path).split(os.sep)
    cur = os.sep if path.startswith(os.sep) else "."
    for part in parts:
        if not part:
            continue
        nxt = os.path.join(cur, part)
        if not os.path.exists(nxt):
            try:
                low = part.lower()
                match = next((e for e in os.listdir(cur) if e.lower() == low), None)
            except OSError:
                match = None
            if match is None:
                return path
            nxt = os.path.join(cur, match)
        cur = nxt
    return cur


def _read_kv(path):
    """Read a .slangp file into (dict, list_of_references)."""
    values = {}
    refs = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            m = re.match(r'#reference\s+"?([^"]+)"?', line)
            if m:
                refs.append(resolve_ci(os.path.normpath(os.path.join(os.path.dirname(path), m.group(1).strip()))))
                continue
            if line.startswith("#") or line.startswith("//"):
                continue
            # strip trailing comments outside of quotes
            line = re.sub(r'\s+(#|//)[^"]*$', "", line)
            if not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip()
            values[k] = (v, os.path.dirname(path))
    return values, refs


def _load_merged(path, depth=0):
    if depth > 16:
        raise PresetError("reference depth exceeded")
    if not os.path.isfile(path):
        raise PresetError(f"preset not found: {path}")
    values, refs = _read_kv(path)
    merged = {}
    for r in refs:
        merged.update(_load_merged(r, depth + 1))
    merged.update(values)
    return merged


def _num(v, default=0.0):
    m = re.match(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)", str(v))
    return float(m.group(1)) if m else default


class Pass:
    def __init__(self):
        self.shader = None
        self.alias = None
        self.filter_linear = None  # None = unspecified
        self.wrap_mode = "clamp_to_border"
        self.mipmap_input = False
        self.float_framebuffer = False
        self.srgb_framebuffer = False
        self.frame_count_mod = 0
        self.scale_type_x = None
        self.scale_type_y = None
        self.scale_x = 1.0
        self.scale_y = 1.0


class Lut:
    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.filter_linear = True
        self.wrap_mode = "clamp_to_border"
        self.mipmap = False


class Preset:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        kv = _load_merged(self.path)

        def get(k, default=None):
            return kv[k][0] if k in kv else default

        def getpath(k):
            v, base = kv[k]
            return resolve_ci(os.path.normpath(os.path.join(base, v.replace("\\", "/"))))

        def getbool(k, default=False):
            v = get(k)
            return default if v is None else v.lower() in TRUE_VALUES

        n = int(get("shaders", "0"))
        if n <= 0:
            raise PresetError("preset has no shaders")
        self.passes = []
        for i in range(n):
            p = Pass()
            p.shader = getpath(f"shader{i}")
            p.alias = get(f"alias{i}") or None
            fl = get(f"filter_linear{i}")
            p.filter_linear = None if fl is None else fl.lower() in TRUE_VALUES
            p.wrap_mode = get(f"wrap_mode{i}", "clamp_to_border")
            p.mipmap_input = getbool(f"mipmap_input{i}")
            p.float_framebuffer = getbool(f"float_framebuffer{i}")
            p.srgb_framebuffer = getbool(f"srgb_framebuffer{i}")
            p.frame_count_mod = int(_num(get(f"frame_count_mod{i}", "0")))
            st = get(f"scale_type{i}")
            p.scale_type_x = get(f"scale_type_x{i}", st)
            p.scale_type_y = get(f"scale_type_y{i}", st)
            sc = get(f"scale{i}")
            p.scale_x = _num(get(f"scale_x{i}", sc if sc is not None else "1.0"), 1.0)
            p.scale_y = _num(get(f"scale_y{i}", sc if sc is not None else "1.0"), 1.0)
            self.passes.append(p)

        self.luts = []
        tex = get("textures")
        if tex:
            for name in [t.strip() for t in tex.split(";") if t.strip()]:
                lut = Lut(name, getpath(name))
                lut.filter_linear = getbool(f"{name}_linear", getbool(f"{name}_filter_linear", True))
                lut.wrap_mode = get(f"{name}_wrap_mode", "clamp_to_border")
                lut.mipmap = getbool(f"{name}_mipmap")
                self.luts.append(lut)

        # every remaining plain key may be a parameter override
        self.overrides = {}
        for k, (v, _) in kv.items():
            if re.match(r"\s*[-+]?(?:\d|\.\d)", v):
                self.overrides[k] = _num(v)


class Parameter:
    def __init__(self, name, label, default, lo, hi, step):
        self.name = name
        self.label = label
        self.default = default
        self.min = lo
        self.max = hi
        self.step = step


class SlangSource:
    """A .slang file with includes resolved and split into stages."""

    def __init__(self, path):
        self.path = path
        lines = self._load(path, 0)
        self.name = None
        self.format = None
        self.parameters = []
        common, vert, frag = [], [], []
        cur = common
        for l in lines:
            m = re.match(r"\s*#pragma\s+stage\s+(\w+)", l)
            if m:
                cur = vert if m.group(1) == "vertex" else frag
                continue
            m = re.match(r"\s*#pragma\s+name\s+(\S+)", l)
            if m:
                self.name = m.group(1)
                continue
            m = re.match(r"\s*#pragma\s+format\s+(\S+)", l)
            if m:
                self.format = m.group(1)
                continue
            m = re.match(r'\s*#pragma\s+parameter\s+(\w+)\s+"([^"]*)"\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+(\S+))?', l)
            if m:
                g = m.groups()
                self.parameters.append(Parameter(g[0], g[1], _num(g[2]), _num(g[3]), _num(g[4]),
                                                 _num(g[5]) if g[5] else 0.0))
                continue
            if re.match(r"\s*#version\b", l):
                continue
            cur.append(l)
        hdr = ["#version 450", "#extension GL_GOOGLE_cpp_style_line_directive : enable"]
        self.vertex = "\n".join(hdr + common + vert) + "\n"
        self.fragment = "\n".join(hdr + common + frag) + "\n"

    def _load(self, path, depth):
        if depth > 32:
            raise PresetError("include depth exceeded")
        out = []
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r'\s*#include\s+"(.+)"', line)
                if m:
                    out += self._load(resolve_ci(os.path.join(os.path.dirname(path), m.group(1))), depth + 1)
                else:
                    out.append(line.rstrip("\n").rstrip("\r"))
        return out
