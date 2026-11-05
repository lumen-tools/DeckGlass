"""Assemble a RetroArch slang preset into a single gamescope-compatible ReShade .fx file."""
import os
import re
import struct
import subprocess
import tempfile
from fractions import Fraction

from .preset import Preset, SlangSource, PresetError
from .xlate import Stage, XlateError, rename_reserved, replace_identifiers

HERE = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(HERE, "bin")


class ConvertError(Exception):
    pass


# ----------------------------------------------------------------------------- tools

def _tool(name):
    p = os.path.join(BIN_DIR, name)
    if os.path.isfile(p):
        return p
    raise ConvertError(f"tool not found: {p}")


def _run(cmd):
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = ""
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise ConvertError((r.stdout + r.stderr).strip()[-4000:])
    return r.stdout


_MEM_CACHE = {}
CACHE_DIR = os.environ.get("SLANGFX_CACHE")


def compile_stage(glsl, stage, workdir):
    import hashlib
    key = hashlib.sha1((stage + "\0" + glsl).encode("utf-8", "replace")).hexdigest()
    if key in _MEM_CACHE:
        return _MEM_CACHE[key]
    cpath = os.path.join(CACHE_DIR, key + ".hlsl") if CACHE_DIR else None
    if cpath and os.path.isfile(cpath):
        with open(cpath, encoding="utf-8") as f:
            res = f.read()
    else:
        res = _compile_stage(glsl, stage, workdir)
        if cpath:
            os.makedirs(CACHE_DIR, exist_ok=True)
            import threading
            tmpname = f"{cpath}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmpname, "w", encoding="utf-8") as f:
                f.write(res)
            os.replace(tmpname, cpath)
    _MEM_CACHE[key] = res
    return res


def _compile_stage(glsl, stage, workdir):
    ext = "vert" if stage == "vertex" else "frag"
    src = os.path.join(workdir, f"s.{ext}")
    spv = os.path.join(workdir, f"s.{ext}.spv")
    with open(src, "w") as f:
        f.write(glsl)
    _run([_tool("glslang"), "-V", "-S", ext, src, "-o", spv])
    return _run([_tool("spirv-cross"), spv, "--hlsl", "--shader-model", "50", "--remove-unused-variables"])


# ----------------------------------------------------------------------------- helpers

def _png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ConvertError(f"LUT is not a PNG: {path}")
    return struct.unpack(">II", head[16:24])


def _scale_expr(base, scale):
    """Integer constant expression for round(base * scale)."""
    fr = Fraction(scale).limit_denominator(1000)
    n, d = fr.numerator, fr.denominator
    if d == 1:
        return f"(({base}) * {n})"
    return f"((({base}) * {2 * n} + {d}) / {2 * d})"


_FORMATS = {
    "R8G8B8A8_UNORM": ("RGBA8", False), "R8G8B8A8_SRGB": ("RGBA8", True),
    "A2B10G10R10_UNORM_PACK32": ("RGB10A2", False),
    "R16G16B16A16_SFLOAT": ("RGBA16F", False), "R32G32B32A32_SFLOAT": ("RGBA32F", False),
    "R16G16B16A16_UNORM": ("RGBA16", False),
    "R8_UNORM": ("R8", False), "R16_SFLOAT": ("R16F", False), "R32_SFLOAT": ("R32F", False),
    "R8G8_UNORM": ("RG8", False), "R16G16_SFLOAT": ("RGBA16F", False), "R32G32_SFLOAT": ("RGBA32F", False),
    "R16_UNORM": ("R16", False), "R16G16_UNORM": ("RG16", False),
}

_WRAP = {"clamp_to_border": "BORDER", "clamp_to_edge": "CLAMP", "repeat": "REPEAT", "mirrored_repeat": "MIRROR"}


def _fmt_float(v):
    s = repr(float(v))
    return s if ("e" in s or "." in s) else s + ".0"


def _clamp_param_indices(code):
    """Parameters are compile-time constants here, so an index like a[int(PRM_x)] with an
    out-of-range value (harmless at runtime in RetroArch) fails to compile. Clamp it."""
    sizes = {m.group(1): int(m.group(2)) for m in re.finditer(r"\b(\w+)\[(\d+)\]\s*(?:=|;)", code)}

    def rep(m):
        name, idx = m.group(1), m.group(2)
        if name in sizes and "PRM_" in idx:
            return f"{name}[clamp(int({idx}), 0, {sizes[name] - 1})]"
        return m.group(0)
    return re.sub(r"\b(\w+)\[([^\[\]]+)\]", rep, code)


MIP_LEVELS = 11  # enough for textures up to 2048 pixels


def _rewrite_lod_reads(code, sampler):
    """tex2Dlod(sampler, float4(uv, 0.0, lod)) -> sampler__lod(uv, lod), which reads the
    emulated mip chain. Returns (code, used)."""
    from .xlate import _find_close, _split_args
    pat = re.compile(r"\btex2Dlod\(\s*" + re.escape(sampler) + r"\s*,")
    out, pos, used = "", 0, False
    while True:
        m = pat.search(code, pos)
        if not m:
            return out + code[pos:], used
        open_i = code.index("(", m.start())
        close = _find_close(code, open_i)
        args = _split_args(code[open_i + 1:close])
        mm = re.match(r"float4\((.*)\)$", args[1].strip(), re.S) if len(args) == 2 else None
        inner = _split_args(mm.group(1)) if mm else []
        if len(inner) == 3:
            out += code[pos:m.start()] + f"{sampler}__lod({inner[0]}, {inner[2]})"
            used = True
        else:
            out += code[pos:close + 1]
        pos = close + 1


def _fix_literal_swizzles(code):
    return re.sub(r"(?<![\w.])(\d+\.\d*(?:[eE][+-]?\d+)?f?|\d+u?)\.([xyzw]{1,4}|[rgba]{1,4})\b", r"(\1).\2", code)


# ----------------------------------------------------------------------------- converter

class Options:
    def __init__(self, native=None, pixel_size=None, params=None, lut_dir="slangfx", defringe=False,
                 bilinear=True):
        """native: (w, h) of the emulated system; the source image is assumed to be
        scaled to fit the screen (aspect preserved, non-integer). pixel_size: manual
        factor (screen pixels per source pixel). Either may be None -> pixel_size 1."""
        self.native = native
        self.pixel_size = pixel_size
        self.params = params or {}
        self.lut_dir = lut_dir
        self.defringe = defringe  # append the Deck OLED (Samsung panel) defringe pass
        self.bilinear = bilinear  # read the game image with bilinear filtering when scaling it down


class PassInfo:
    pass


def defringe_code(src_texture):
    """Deck OLED (Samsung panel) defringe, after the shader shipped with Reshadeck:
    nudges red left, green right and blue down by a fraction of a pixel."""
    return [
        f"sampler2D S_DEFRINGE {{ Texture = {src_texture}; MagFilter = POINT; MinFilter = POINT; MipFilter = POINT; "
        "AddressU = CLAMP; AddressV = CLAMP; };",
        "float4 SFX_PS_Defringe(float4 pos : SV_Position, float2 uv : TEXCOORD0) : SV_Target",
        "{",
        "    float2 px = float2(BUFFER_RCP_WIDTH, BUFFER_RCP_HEIGHT);",
        "    float3 c = tex2D(S_DEFRINGE, uv).rgb;",
        "    float3 right = tex2D(S_DEFRINGE, uv + float2(px.x, 0.0)).rgb;",
        "    float3 left = tex2D(S_DEFRINGE, uv - float2(px.x, 0.0)).rgb;",
        "    float3 bottom = tex2D(S_DEFRINGE, uv + float2(0.0, px.y)).rgb;",
        "    float3 o;",
        "    o.r = lerp(c.r, left.r, 0.4);",
        "    o.g = lerp(c.g, right.g, 0.2);",
        "    o.b = lerp(c.b, bottom.b, 0.3);",
        "    return float4(o, 1.0);",
        "}",
    ]


def defringe_only_fx():
    """A standalone effect with just the OLED defringe pass."""
    lines = [
        "// Generated by slangfx: Deck OLED defringe only.",
        "texture T_BACK : COLOR;",
        "void SFX_VS(in uint id : SV_VertexID, out float4 pos : SV_Position, out float2 uv : TEXCOORD0)",
        "{",
        "    uv = float2((id == 2) ? 2.0 : 0.0, (id == 1) ? 2.0 : 0.0);",
        "    pos = float4(uv * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);",
        "}",
    ] + defringe_code("T_BACK") + [
        "technique SlangFX",
        "{",
        "    pass Defringe { VertexShader = SFX_VS; PixelShader = SFX_PS_Defringe; }",
        "}",
    ]
    return "\n".join(lines) + "\n"


def convert(preset_path, options=None, workdir=None):
    """Returns (fx_source, luts, parameters) where luts is a list of (src_path, rel_dst_path)."""
    options = options or Options()
    try:
        preset = Preset(preset_path)
    except (PresetError, OSError, ValueError) as e:
        raise ConvertError(f"preset: {e}")
    n = len(preset.passes)
    own_tmp = workdir is None
    tmp = tempfile.TemporaryDirectory() if own_tmp else None
    wd = tmp.name if own_tmp else workdir
    try:
        return _convert(preset, options, wd)
    finally:
        if tmp:
            tmp.cleanup()


def _convert(preset, options, wd):
    n = len(preset.passes)
    infos = []
    params = {}
    param_order = []
    for i, p in enumerate(preset.passes):
        try:
            src = SlangSource(p.shader)
        except OSError as e:
            raise ConvertError(f"pass {i}: {e}")
        info = PassInfo()
        info.index = i
        info.p = p
        info.src = src
        info.alias = p.alias or src.name
        fmt = src.format
        if fmt and fmt in _FORMATS:
            info.format, info.srgb = _FORMATS[fmt]
        elif p.float_framebuffer:
            info.format, info.srgb = "RGBA16F", False
        elif p.srgb_framebuffer:
            info.format, info.srgb = "RGBA8", True
        else:
            info.format, info.srgb = "RGBA8", False
        for prm in src.parameters:
            if prm.name not in params:
                params[prm.name] = prm
                param_order.append(prm.name)
        try:
            info.vs = Stage(compile_stage(src.vertex, "vertex", wd), "vertex")
            info.fs = Stage(compile_stage(src.fragment, "fragment", wd), "fragment")
        except (ConvertError, XlateError) as e:
            raise ConvertError(f"pass {i} ({os.path.basename(p.shader)}): {e}")
        infos.append(info)

    aliases = {info.alias: info.index for info in infos if info.alias}
    luts = {l.name: l for l in preset.luts}

    # ---- texture name resolution ---------------------------------------------------------
    history_max = 0
    feedback = set()

    def resolve(i, name):
        """-> (kind, key). kind in orig/pass/fb/hist/lut."""
        nonlocal history_max
        if name == "Source":
            return ("orig", None) if i == 0 else ("pass", i - 1)
        if name in ("Original", "OriginalHistory0"):
            return ("orig", None)
        m = re.fullmatch(r"OriginalHistory(\d+)", name)
        if m:
            k = int(m.group(1))
            history_max = max(history_max, k)
            return ("hist", k)
        m = re.fullmatch(r"PassOutput(\d+)", name)
        if m:
            return ("pass", int(m.group(1)))
        m = re.fullmatch(r"PassFeedback(\d+)", name)
        if m:
            feedback.add(int(m.group(1)))
            return ("fb", int(m.group(1)))
        if name in luts:
            return ("lut", name)
        if name in aliases:
            return ("pass", aliases[name])
        if name.endswith("Feedback") and name[:-8] in aliases:
            k = aliases[name[:-8]]
            feedback.add(k)
            return ("fb", k)
        raise ConvertError(f"pass {i}: unknown texture '{name}'")

    for info in infos:
        info.tex_refs = {}
        for st in (info.vs, info.fs):
            for t in st.textures:
                info.tex_refs[t] = resolve(info.index, t)

    # ---- sizes ------------------------------------------------------------------------------
    last = infos[-1]
    lines_size = []
    for info in infos:
        p = info.p
        final = info.index == n - 1
        stx = p.scale_type_x or ("viewport" if final else "source")
        sty = p.scale_type_y or ("viewport" if final else "source")
        in_w = "SFX_ORIG_W" if info.index == 0 else f"SFX_P{info.index - 1}_W"
        in_h = "SFX_ORIG_H" if info.index == 0 else f"SFX_P{info.index - 1}_H"
        exprs = []
        for st, sc, base, buf in ((stx, p.scale_x, in_w, "BUFFER_WIDTH"), (sty, p.scale_y, in_h, "BUFFER_HEIGHT")):
            if st == "absolute":
                exprs.append(str(max(1, int(round(sc)))))
            elif st == "viewport":
                exprs.append(_scale_expr(buf, sc))
            else:
                exprs.append(_scale_expr(base, sc))
        lines_size.append(f"#define SFX_P{info.index}_W {exprs[0]}")
        lines_size.append(f"#define SFX_P{info.index}_H {exprs[1]}")
        info.is_viewport_1 = stx == "viewport" and sty == "viewport" and p.scale_x == 1.0 and p.scale_y == 1.0
    # the final pass can write to the back buffer directly unless something reads it back
    direct = last.is_viewport_1 and (n - 1) not in feedback and not options.defringe and not any(
        r == ("pass", n - 1) for info in infos for r in info.tex_refs.values())
    # with defringe, the finished image must sit in a screen-sized texture first
    defringe_src = None
    if options.defringe:
        defringe_src = f"T_P{n - 1}" if last.is_viewport_1 else "T_OUT"

    def size_of(ref):
        kind, key = ref
        if kind in ("orig", "hist"):
            return "SFX_ORIG_W", "SFX_ORIG_H"
        if kind in ("pass", "fb"):
            return f"SFX_P{key}_W", f"SFX_P{key}_H"
        if kind == "lut":
            w, h = _png_size(luts[key].path)
            return str(w), str(h)
        raise AssertionError

    def tex_name(ref):
        kind, key = ref
        return {"orig": "T_ORIG", "pass": f"T_P{key}", "fb": f"T_FB{key}", "hist": f"T_H{key}",
                "lut": f"T_LUT_{key}"}[kind]

    def is_srgb(ref):
        kind, key = ref
        return kind in ("pass", "fb") and infos[key].srgb

    # gamescope only supports textures with a single mip level (it asserts otherwise),
    # so mipmap_input / LUT mipmaps are ignored.

    # ---- parameters ----------------------------------------------------------------------------
    param_values = {}
    for name in param_order:
        prm = params[name]
        v = prm.default
        if name in preset.overrides:
            v = preset.overrides[name]
        if name in options.params:
            v = float(options.params[name])
        param_values[name] = v

    # ---- emit -------------------------------------------------------------------------------------
    out = []
    w = out.append
    w("// Generated by slangfx from a RetroArch slang preset. Do not edit by hand.")
    w(f"// Preset: {os.path.basename(preset.path)}")
    w("")
    if options.native:
        nw, nh = options.native
        w(f"// source image: {nw}x{nh}, scaled to fit the screen")
        w(f"#if BUFFER_WIDTH * {nh} <= BUFFER_HEIGHT * {nw}")
        w(f"  #define SFX_ORIG_W {nw}")
        w(f"  #define SFX_ORIG_H ((BUFFER_HEIGHT * {2 * nw} + BUFFER_WIDTH) / (2 * BUFFER_WIDTH))")
        w("#else")
        w(f"  #define SFX_ORIG_W ((BUFFER_WIDTH * {2 * nh} + BUFFER_HEIGHT) / (2 * BUFFER_HEIGHT))")
        w(f"  #define SFX_ORIG_H {nh}")
        w("#endif")
    else:
        ps = options.pixel_size or 1.0
        inv = 1.0 / ps
        w(f"// pixel size {ps}")
        w(f"#define SFX_ORIG_W {_scale_expr('BUFFER_WIDTH', inv)}")
        w(f"#define SFX_ORIG_H {_scale_expr('BUFFER_HEIGHT', inv)}")
    out.extend(lines_size)
    w("")
    w('uniform int SFX_FRAMECOUNT < source = "framecount"; >;')
    w("")
    for name in param_order:
        w(f"static const float PRM_{name} = {_fmt_float(param_values[name])};")
    w("")

    # textures
    def mip(tn):
        return ""

    w("texture T_BACK : COLOR;")
    back_filter = "LINEAR" if options.bilinear else "POINT"
    w(f"sampler2D S_BACK {{ Texture = T_BACK; MagFilter = {back_filter}; MinFilter = {back_filter}; MipFilter = POINT; "
      "AddressU = CLAMP; AddressV = CLAMP; };")
    w(f"texture T_ORIG {{ Width = SFX_ORIG_W; Height = SFX_ORIG_H; Format = RGBA8;{mip('T_ORIG')} }};")
    for k in range(1, history_max + 1):
        w(f"texture T_H{k} {{ Width = SFX_ORIG_W; Height = SFX_ORIG_H; Format = RGBA8;{mip(f'T_H{k}')} }};")
    for info in infos:
        k = info.index
        if k == n - 1 and direct:
            continue
        w(f"texture T_P{k} {{ Width = SFX_P{k}_W; Height = SFX_P{k}_H; Format = {info.format};{mip(f'T_P{k}')} }};")
    for k in sorted(feedback):
        w(f"texture T_FB{k} {{ Width = SFX_P{k}_W; Height = SFX_P{k}_H; Format = {infos[k].format};{mip(f'T_FB{k}')} }};")
    lut_files = []
    for name, lut in luts.items():
        lw, lh = _png_size(lut.path)
        rel = f"{options.lut_dir}/{os.path.basename(lut.path)}"
        lut_files.append((lut.path, rel))
        w(f'texture T_LUT_{name} < source = "{rel}"; > {{ Width = {lw}; Height = {lh}; Format = RGBA8; }};')
    w("")

    # shared vertex shader + copy helpers
    w("void SFX_VS(in uint id : SV_VertexID, out float4 pos : SV_Position, out float2 uv : TEXCOORD0)")
    w("{")
    w("    uv = float2((id == 2) ? 2.0 : 0.0, (id == 1) ? 2.0 : 0.0);")
    w("    pos = float4(uv * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);")
    w("}")
    w("float4 SFX_PS_Down(float4 pos : SV_Position, float2 uv : TEXCOORD0) : SV_Target")
    w("{")
    w("    return float4(tex2D(S_BACK, uv).rgb, 1.0);")
    w("}")

    copies = []  # (dst_tex, src_tex, name)

    def add_copy(dst, src):
        name = f"SFX_PS_Copy_{src}_{dst}"
        w(f"sampler2D S_COPY_{src}_{dst} {{ Texture = {src}; MagFilter = POINT; MinFilter = POINT; MipFilter = POINT; }};")
        w(f"float4 {name}(float4 pos : SV_Position, float2 uv : TEXCOORD0) : SV_Target")
        w("{")
        w(f"    return tex2Dfetch(S_COPY_{src}_{dst}, int2(pos.xy), 0);")
        w("}")
        copies.append((dst, src, name))

    for k in sorted(feedback):
        add_copy(f"T_FB{k}", f"T_P{k}")
    for k in range(history_max, 0, -1):
        add_copy(f"T_H{k}", "T_ORIG" if k == 1 else f"T_H{k - 1}")
    need_blit = not direct and defringe_src != f"T_P{n - 1}"
    if need_blit:
        w(f"sampler2D S_BLIT {{ Texture = T_P{n - 1}; MagFilter = LINEAR; MinFilter = LINEAR; MipFilter = LINEAR; }};")
        w("float4 SFX_PS_Blit(float4 pos : SV_Position, float2 uv : TEXCOORD0) : SV_Target")
        w("{")
        w("    return float4(tex2D(S_BLIT, uv).rgb, 1.0);")
        w("}")
    if defringe_src == "T_OUT":
        w("texture T_OUT { Width = BUFFER_WIDTH; Height = BUFFER_HEIGHT; Format = RGBA8; };")
    if defringe_src:
        out.extend(defringe_code(defringe_src))
    w("")

    # gamescope has no mipmaps. Shaders that read explicit mip levels of a
    # mipmap_input texture (e.g. crt-geom-deluxe's average brightness at LOD 9) get an
    # emulated chain: each level halves the previous one with a 2x2 box filter.
    mip_chains = {}        # texture name -> (width macro, height macro)
    pass_mips = {}         # pass index -> [texture names whose chain must be built first]
    for info in infos:
        if not info.p.mipmap_input:
            continue
        for st in (info.vs, info.fs):
            for t in st.textures:
                ref = info.tex_refs[t]
                if ref[0] == "lut" or not re.search(r"\btex2Dlod\(\s*" + re.escape(t) + r"\s*,", st.body):
                    continue
                tn = tex_name(ref)
                if tn not in mip_chains:
                    mip_chains[tn] = size_of(ref)
                pass_mips.setdefault(info.index, [])
                if tn not in pass_mips[info.index]:
                    pass_mips[info.index].append(tn)
    for tn, (bw, bh) in mip_chains.items():
        for lv in range(1, MIP_LEVELS + 1):
            d = 2 ** lv
            fmt = "RGBA16F"
            w(f"texture {tn}_M{lv} {{ Width = (({bw}) + {d - 1}) / {d}; Height = (({bh}) + {d - 1}) / {d}; Format = {fmt}; }};")
            prev = tn if lv == 1 else f"{tn}_M{lv - 1}"
            w(f"sampler2D S_{tn}_R{lv} {{ Texture = {prev}; MagFilter = LINEAR; MinFilter = LINEAR; MipFilter = POINT; "
              "AddressU = CLAMP; AddressV = CLAMP; };")
            w(f"float4 SFX_PS_{tn}_M{lv}(float4 pos : SV_Position, float2 uv : TEXCOORD0) : SV_Target")
            w("{")
            w(f"    return tex2D(S_{tn}_R{lv}, uv);")
            w("}")
    w("")

    # passes
    for info in infos:
        k = info.index
        final_direct = (k == n - 1) and direct
        out_w = "BUFFER_WIDTH" if final_direct else f"SFX_P{k}_W"
        out_h = "BUFFER_HEIGHT" if final_direct else f"SFX_P{k}_H"

        def uniform_map(stage):
            mp = {}
            for u in stage.uniforms:
                rep = None
                parts = u.split("_")
                for j in range(1, len(parts)):
                    mem = "_".join(parts[j:])
                    rep = _uniform_value(mem)
                    if rep is not None:
                        break
                if rep is None:
                    rep = _uniform_value(u)
                if rep is None:
                    # RetroArch leaves members without a matching parameter at zero
                    rep = f"(({stage.uniform_types.get(u, 'float')})0)"
                mp[u] = rep
            return mp

        def vec_size(wexp, hexp):
            return (f"float4(float({wexp}), float({hexp}), 1.0 / float({wexp}), 1.0 / float({hexp}))")

        def _uniform_value(mem):
            if mem == "MVP":
                return "float4x4(1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)"
            if mem == "SourceSize":
                return vec_size(*size_of(("orig", None) if k == 0 else ("pass", k - 1)))
            if mem in ("OriginalSize",) or re.fullmatch(r"OriginalHistorySize\d+", mem):
                return vec_size("SFX_ORIG_W", "SFX_ORIG_H")
            if mem == "OutputSize":
                return vec_size(out_w, out_h)
            if mem == "FinalViewportSize":
                return vec_size("BUFFER_WIDTH", "BUFFER_HEIGHT")
            if mem == "FrameCount":
                mod = info.p.frame_count_mod
                return f"(uint(SFX_FRAMECOUNT) % {mod}u)" if mod > 0 else "uint(SFX_FRAMECOUNT)"
            if mem == "FrameDirection":
                return "1"
            if mem in ("Rotation",):
                return "0u"
            if mem in ("TotalSubFrames", "CurrentSubFrame"):
                return "1u"
            if mem in ("OriginalFPS", "CoreFPS"):
                return "60.0"
            if mem == "FrameTimeDelta":
                return "16667u"
            if mem in ("OriginalAspect", "OriginalAspectRotated"):
                return "(float(SFX_ORIG_W) / float(SFX_ORIG_H))"
            m = re.fullmatch(r"PassOutputSize(\d+)", mem) or re.fullmatch(r"PassFeedbackSize(\d+)", mem)
            if m:
                j = int(m.group(1))
                if j == n - 1 and direct:
                    return vec_size("BUFFER_WIDTH", "BUFFER_HEIGHT")
                return vec_size(f"SFX_P{j}_W", f"SFX_P{j}_H")
            if mem.endswith("Size"):
                base = mem[:-4]
                if base in luts:
                    return vec_size(*size_of(("lut", base)))
                b2 = base[:-8] if base.endswith("Feedback") else base
                if b2 in aliases:
                    j = aliases[b2]
                    if j == n - 1 and direct:
                        return vec_size("BUFFER_WIDTH", "BUFFER_HEIGHT")
                    return vec_size(f"SFX_P{j}_W", f"SFX_P{j}_H")
            if mem in params:
                return f"PRM_{mem}"
            return None

        def samplers(stage):
            res = []
            filt = "LINEAR" if info.p.filter_linear else "POINT"
            mipf = "POINT"
            for t in stage.textures:
                ref = info.tex_refs[t]
                if ref[0] == "lut":
                    lut = luts[ref[1]]
                    f = "LINEAR" if lut.filter_linear else "POINT"
                    wrap = _WRAP.get(lut.wrap_mode, "BORDER")
                    mf = "POINT"
                    srgb = ""
                else:
                    f = filt
                    wrap = _WRAP.get(info.p.wrap_mode, "BORDER")
                    mf = mipf
                    srgb = " SRGBTexture = true;" if is_srgb(ref) else ""
                tn = tex_name(ref)
                if ref == ("pass", n - 1) and direct:
                    raise AssertionError
                res.append(f"    sampler2D {rename_reserved(t)} {{ Texture = {tn}; MagFilter = {f}; MinFilter = {f}; "
                           f"MipFilter = {mf}; AddressU = {wrap}; AddressV = {wrap};{srgb} }};")
            return res

        for stage, ns in ((info.vs, f"P{k}_V"), (info.fs, f"P{k}_F")):
            body = stage.body
            body = _clamp_param_indices(replace_identifiers(body, uniform_map(stage)))
            body = _fix_literal_swizzles(rename_reserved(body))
            main = _fix_literal_swizzles(rename_reserved(stage.main_body))
            mip_code = []
            for t in stage.textures:
                ref = info.tex_refs[t]
                tn = tex_name(ref) if ref[0] != "lut" else None
                if tn not in mip_chains or tn not in pass_mips.get(k, []):
                    continue
                sname = rename_reserved(t)
                body, used = _rewrite_lod_reads(body, sname)
                if not used:
                    continue
                for lv in range(1, MIP_LEVELS + 1):
                    mip_code.append(f"    sampler2D {sname}__m{lv} {{ Texture = {tn}_M{lv}; MagFilter = LINEAR; "
                                    "MinFilter = LINEAR; MipFilter = POINT; AddressU = CLAMP; AddressV = CLAMP; };")
                mip_code.append(f"    float4 {sname}__lvl(float2 uv, float l)")
                mip_code.append("    {")
                mip_code.append(f"        if (l < 0.5) return tex2Dlod({sname}, float4(uv, 0.0, 0.0));")
                for lv in range(1, MIP_LEVELS):
                    mip_code.append(f"        if (l < {lv}.5) return tex2Dlod({sname}__m{lv}, float4(uv, 0.0, 0.0));")
                mip_code.append(f"        return tex2Dlod({sname}__m{MIP_LEVELS}, float4(uv, 0.0, 0.0));")
                mip_code.append("    }")
                mip_code.append(f"    float4 {sname}__lod(float2 uv, float lod)")
                mip_code.append("    {")
                mip_code.append(f"        lod = clamp(lod, 0.0, {MIP_LEVELS}.0);")
                mip_code.append("        float l = floor(lod);")
                mip_code.append(f"        return lerp({sname}__lvl(uv, l), {sname}__lvl(uv, l + 1.0), lod - l);")
                mip_code.append("    }")
            w(f"namespace {ns}")
            w("{")
            out.extend(samplers(stage))
            out.extend(mip_code)
            w(body)
            if stage.stage == "vertex":
                params_sig = ["in uint _sfx_id : SV_VertexID", "out float4 _sfx_pos : SV_Position"]
                fs_sems = {m["semantic"] for m in info.fs.inputs}
                vs_sems = set()
                for m in stage.outputs:
                    if m["semantic"].upper() == "SV_POSITION":
                        continue
                    vs_sems.add(m["semantic"])
                    mods = (m["mods"] + " ") if m["mods"] else ""
                    params_sig.append(f"out {mods}{m['type']} _out_{m['name']}{m['array']} : {m['semantic']}")
                extra = []
                for m in info.fs.inputs:
                    if m["semantic"].upper() == "SV_POSITION" or m["semantic"] in vs_sems:
                        continue
                    mods = (m["mods"] + " ") if m["mods"] else ""
                    params_sig.append(f"out {mods}{m['type']} _sfx_extra_{m['name']} : {m['semantic']}")
                    extra.append(f"    _sfx_extra_{m['name']} = ({m['type']})0;")
                w(f"    void VS({', '.join(params_sig)})")
                w("    {")
                w("        float2 _sfx_tc = float2((_sfx_id == 2) ? 2.0 : 0.0, (_sfx_id == 1) ? 2.0 : 0.0);")
                if any(m["name"] == "gl_Position" for m in stage.outputs):
                    w("        float4 _out_gl_Position;")
                for m in stage.inputs:
                    t = m["type"]
                    if m["semantic"].upper() == "SV_VERTEXID":
                        val = f"{t}(_sfx_id)"
                    elif t == "float4":
                        val = "float4(_sfx_tc, 0.0, 1.0)"
                    elif t == "float2":
                        val = "_sfx_tc"
                    elif t == "float3":
                        val = "float3(_sfx_tc, 0.0)"
                    else:
                        val = f"({t})0"
                    w(f"        {t} _in_{m['name']} = {val};")
                w(main)
                out.extend(extra)
                w("        _sfx_pos = float4(_sfx_tc * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);")
                w("    }")
            else:
                params_sig = ["float4 _sfx_vpos : SV_Position"]
                pre = []
                for m in stage.inputs:
                    if m["semantic"].upper() == "SV_POSITION":
                        pre.append(f"        float4 _in_{m['name']} = _sfx_vpos;")
                        continue
                    mods = (m["mods"] + " ") if m["mods"] else ""
                    params_sig.append(f"in {mods}{m['type']} _in_{m['name']}{m['array']} : {m['semantic']}")
                outs = [m for m in stage.outputs if m["semantic"].upper().startswith("SV_TARGET")]
                if len(outs) != 1:
                    raise ConvertError(f"pass {k}: expected exactly one fragment output")
                o = outs[0]
                params_sig.append(f"out {o['type']} _out_{o['name']} : SV_Target")
                w(f"    void PS({', '.join(params_sig)})")
                w("    {")
                out.extend(pre)
                w(main)
                if final_direct:
                    w(f"        _out_{o['name']}.a = 1.0;")
                w("    }")
            w("}")
        w("")

    # technique
    w("technique SlangFX")
    w("{")
    # Feedback/history copies run first: the targets still hold the previous frame here,
    # which is exactly what PassFeedbackN / OriginalHistoryN mean. gamescope displays the
    # render target of the last pass, so the final image pass must come last.
    for dst, src, name in copies:
        w(f"    pass {{ VertexShader = SFX_VS; PixelShader = {name}; RenderTarget = {dst}; }}")
    w("    pass Downsample { VertexShader = SFX_VS; PixelShader = SFX_PS_Down; RenderTarget = T_ORIG; }")
    for info in infos:
        k = info.index
        rt = "" if (k == n - 1 and direct) else f" RenderTarget = T_P{k};"
        srgb = " SRGBWriteEnable = true;" if info.srgb and rt else ""
        for tn in pass_mips.get(k, []):
            for lv in range(1, MIP_LEVELS + 1):
                w(f"    pass {{ VertexShader = SFX_VS; PixelShader = SFX_PS_{tn}_M{lv}; RenderTarget = {tn}_M{lv}; }}")
        w(f"    pass P{k} {{ VertexShader = P{k}_V::VS; PixelShader = P{k}_F::PS;{rt}{srgb} }}")
    if need_blit:
        rt = " RenderTarget = T_OUT;" if defringe_src == "T_OUT" else ""
        w(f"    pass Blit {{ VertexShader = SFX_VS; PixelShader = SFX_PS_Blit;{rt} }}")
    if defringe_src:
        w("    pass Defringe { VertexShader = SFX_VS; PixelShader = SFX_PS_Defringe; }")
    w("}")

    plist = [{"name": nme, "label": params[nme].label, "default": params[nme].default, "min": params[nme].min,
              "max": params[nme].max, "step": params[nme].step, "value": param_values[nme]} for nme in param_order]
    return "\n".join(out) + "\n", lut_files, plist


def list_parameters(preset_path, user_params=None):
    """Parameters of a preset with their effective values, without compiling anything."""
    preset = Preset(preset_path)
    user_params = user_params or {}
    seen, res = set(), []
    for p in preset.passes:
        for prm in SlangSource(p.shader).parameters:
            if prm.name in seen:
                continue
            seen.add(prm.name)
            v = preset.overrides.get(prm.name, prm.default)
            v = float(user_params.get(prm.name, v))
            entry = {"name": prm.name, "label": prm.label.strip(), "default": prm.default, "min": prm.min,
                     "max": prm.max, "step": prm.step, "value": v}
            entry.update(describe_parameter(prm.label, prm.min, prm.max, prm.step))
            res.append(entry)
    return res


def _is_int(x):
    return abs(x - round(x)) < 1e-6


def describe_parameter(label, lo, hi, step):
    """Explain integer parameters whose meaning is listed in the label, e.g.
    "NTSC Merge Fields: Auto | NO | YES" for -1..1. The value stays a number;
    this only adds names for display. Returns {"kind", "label", "options"}."""
    text = label.strip()
    if lo == hi or text.startswith("["):
        # section titles such as "[ AFTERGLOW SETTINGS ]:" (sometimes with a dummy range)
        return {"kind": "heading", "label": text.strip("[]:=-*# ").strip() or text}
    integral = _is_int(lo) and _is_int(hi) and (step <= 0 or _is_int(step)) and hi - lo <= 16
    if not integral:
        return {"kind": "slider", "label": text}
    values = list(range(int(round(lo)), int(round(hi)) + 1, max(1, int(round(step))) if step > 0 else 1))

    def choice(head, names):
        if len(names) >= 2 and len(names) == len(values) - 1 and values[0] == 0:
            names = ["Off"] + names  # e.g. "LUT Colors: NTSC-J | Trinit. | ..." for 0..4, where 0 is off
        if len(names) != len(values) or len(names) < 2:
            return None
        return {"kind": "choice", "label": head or text,
                "options": [{"value": float(x), "label": n} for x, n in zip(values, names)]}

    # 1) explicit "0: name, 1: name" / "0=name" lists
    explicit = re.findall(r"(-?\d+)\s*[:=]\s*([^,;|()]+?)(?=\s*(?:[,;|)]|-?\d+\s*[:=]|$))", text)
    if len(explicit) >= 2:
        names = {int(k): n.strip() for k, n in explicit}
        if set(names) == set(values):
            base = re.split(r"\s*[(\[]?\s*-?\d+\s*[:=]", text, maxsplit=1)[0].strip(" :-(")
            return {"kind": "choice", "label": base or text,
                    "options": [{"value": float(x), "label": names[x]} for x in values]}

    # 2) "Name: a | b | c", "Name: a, b, c", "Name: a / b", "Name: a or b"
    m = re.match(r"^(.*?):\s*(.+)$", text)
    if m:
        head, tail = m.group(1).strip(), m.group(2).strip()
        for sep in (r"\s*\|\s*", r"\s*,\s*", r"\s*/\s*", r"\s+or\s+"):
            parts = [p.strip() for p in re.split(sep, tail) if p.strip()]
            if all(len(p) <= 24 for p in parts):
                r = choice(head, parts)
                if r:
                    return r
    # 3) "Name (a/b/c)"
    m = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", text)
    if m:
        r = choice(m.group(1).strip(), [p.strip() for p in re.split(r"\s*[/|,]\s*", m.group(2)) if p.strip()])
        if r:
            return r
    return {"kind": "slider", "label": text}