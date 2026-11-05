"""Turn SPIRV-Cross HLSL (SM 5.0) output into ReShade FX code fragments."""
import re

# Identifiers that ReShade FX reserves but which may appear as plain names in
# translated shaders. Types and statements we emit ourselves stay allowed.
_RESHADE_RESERVED = set("""
asm asm_fragment auto cast catch char class column_major compile const_cast delete double dword
dynamic_cast enum explicit external friend globallycoherent goto half inline interface long matrix
mutable new noinline operator packed packoffset pass private protected public register
reinterpret_cast restrict sample sampler sampler1D sampler1DArray sampler2DArray sampler2DMS
sampler2DMSArray sampler3D sampler_state samplerCube samplerCubeArray samplerCUBE samplerRect
samplerRECT SamplerState storage storage1D storage2D storage3D shared short signed sizeof snorm
static_cast string technique template texture Texture1D texture1D Texture1DArray texture2D
Texture2DArray Texture2DMS Texture2DMSArray Texture3D texture3D textureCUBE TextureCube
TextureCubeArray textureRECT this try typedef union unorm unsigned using vector virtual
""".split())


class XlateError(Exception):
    pass


def _find_close(s, start):
    """s[start] == '(' -> index of the matching ')'."""
    depth = 0
    for i in range(start, len(s)):
        c = s[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
    raise XlateError("unbalanced parentheses")


def _split_args(s):
    args, depth, cur = [], 0, ""
    for c in s:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
        else:
            cur += c
    if cur.strip():
        args.append(cur.strip())
    return args


def _remove_block(src, header_regex):
    """Remove `header { ... };`-style blocks. Returns (src, [bodies])."""
    bodies = []
    while True:
        m = re.search(header_regex, src, re.M)
        if not m:
            return src, bodies
        open_i = src.index("{", m.end() - 1)
        depth, i = 0, open_i
        while True:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        end = i + 1
        if end < len(src) and src[end] == ";":
            end += 1
        bodies.append(src[open_i + 1:i])
        src = src[:m.start()] + src[end:]


def _parse_members(body):
    members = []
    for line in body.split(";"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"((?:\w+\s+)*?)(\w+)\s+(\w+)(\[\d+\])?\s*:\s*(\w+)", line)
        if m:
            members.append({"mods": m.group(1).strip(), "type": m.group(2), "name": m.group(3),
                            "array": m.group(4) or "", "semantic": m.group(5)})
    return members


_TEX_METHODS = ("SampleLevel", "SampleGrad", "SampleBias", "SampleCmpLevelZero", "SampleCmp", "Sample",
                "Load", "GatherRed", "GatherGreen", "GatherBlue", "GatherAlpha", "Gather", "GetDimensions")


def _rewrite_texture_methods(src):
    pat = re.compile(r"\b(\w+)\.(" + "|".join(_TEX_METHODS) + r")\(")
    out, pos = "", 0
    while True:
        m = pat.search(src, pos)
        if not m:
            return out + src[pos:]
        close = _find_close(src, m.end() - 1)
        args = _split_args(src[m.end():close])
        tex, meth = m.group(1), m.group(2)
        args = [_rewrite_texture_methods(a) for a in args]
        if meth == "Sample":
            # (sampler, uv [, offset])
            rep = f"tex2D({tex}, {args[1]}" + (f", {args[2]}" if len(args) > 2 else "") + ")"
        elif meth == "SampleLevel":
            rep = f"tex2Dlod({tex}, float4({args[1]}, 0.0, {args[2]})" + (f", {args[3]}" if len(args) > 3 else "") + ")"
        elif meth == "SampleBias":
            # No bias intrinsic in ReShade FX: approximate with plain sampling.
            rep = f"tex2D({tex}, {args[1]}" + (f", {args[3]}" if len(args) > 3 else "") + ")"
        elif meth == "SampleGrad":
            rep = f"tex2Dgrad({tex}, {args[1]}, {args[2]}, {args[3]}" + (f", {args[4]}" if len(args) > 4 else "") + ")"
        elif meth == "Load":
            # Load(int3(coord, lod) [, offset])
            inner = args[0]
            mm = re.match(r"int3\((.*)\)$", inner, re.S)
            if mm:
                parts = _split_args(mm.group(1))
                if len(parts) == 2:
                    coord, lod = parts
                elif len(parts) == 3:
                    coord, lod = f"int2({parts[0]}, {parts[1]})", parts[2]
                else:
                    raise XlateError("unexpected Load() form")
            else:
                coord, lod = f"({inner}).xy", f"({inner}).z"
            if len(args) > 1:
                coord = f"({coord}) + ({args[1]})"
            rep = f"tex2Dfetch({tex}, {coord}, {lod})"
        elif meth.startswith("Gather"):
            ch = {"Gather": "R", "GatherRed": "R", "GatherGreen": "G", "GatherBlue": "B", "GatherAlpha": "A"}[meth]
            rep = f"tex2Dgather{ch}({tex}, {args[1]}" + (f", {args[2]}" if len(args) > 2 else "") + ")"
        elif meth == "GetDimensions":
            raise XlateError("GetDimensions outside spvTextureSize")
        else:
            raise XlateError(f"unsupported texture method {meth}")
        out += src[pos:m.start()] + rep
        pos = close + 1


_SPV_TEXSIZE = re.compile(r"uint2 spvTextureSize\(Texture2D<\w+> Tex, uint Level, out uint Param\)\s*\{.*?\n\}", re.S)
_SPV_TEXSIZE_NOLOD = re.compile(r"uint2 spvTextureSize\(Texture2D<\w+> Tex, out uint Param\)\s*\{.*?\n\}", re.S)


class Stage:
    """Parsed result of one shader stage."""

    def __init__(self, hlsl, stage):
        self.stage = stage
        src = hlsl.replace("\r", "")

        # helper functions that use GetDimensions
        src = _SPV_TEXSIZE.sub(
            "uint2 spvTextureSize(sampler2D Tex, uint Level, out uint Param)\n{\n    Param = 1u;\n"
            "    return uint2(tex2Dsize(Tex, int(Level)));\n}", src)
        src = _SPV_TEXSIZE_NOLOD.sub(
            "uint2 spvTextureSize(sampler2D Tex, out uint Param)\n{\n    Param = 1u;\n"
            "    return uint2(tex2Dsize(Tex));\n}", src)

        # uniform blocks -> remember member names
        self.uniforms = []
        self.uniform_types = {}
        src, bodies = _remove_block(src, r"^cbuffer\s+\w+(?:\s*:\s*register\(\w+\))?\s*\{")
        for b in bodies:
            for line in b.split(";"):
                m = re.match(r"\s*(?:row_major\s+|column_major\s+)?(\w+)\s+(\w+)(\[\d+\])?\s*:\s*packoffset", line)
                if m:
                    self.uniforms.append(m.group(2))
                    self.uniform_types[m.group(2)] = m.group(1)

        # global textures / samplers
        self.textures = re.findall(r"^Texture2D<\w+>\s+(\w+)\s*:\s*register\(\w+\)\s*;", src, re.M)
        src = re.sub(r"^Texture2D<\w+>\s+\w+\s*:\s*register\(\w+\)\s*;\n?", "", src, flags=re.M)
        src = re.sub(r"^SamplerState\s+\w+\s*:\s*register\(\w+\)\s*;\n?", "", src, flags=re.M)
        if re.search(r"^Texture(?!2D<)\w*", src, re.M):
            raise XlateError("unsupported texture type (only 2D textures are supported)")

        # stage IO structs
        src, ins = _remove_block(src, r"^struct SPIRV_Cross_Input\s*\{")
        src, outs = _remove_block(src, r"^struct SPIRV_Cross_Output\s*\{")
        self.inputs = _parse_members(ins[0]) if ins else []
        self.outputs = _parse_members(outs[0]) if outs else []

        # entry point
        m = re.search(r"^(?:SPIRV_Cross_Output|void)\s+main\((.*?)\)\s*\{", src, re.M)
        if not m:
            raise XlateError("entry point not found")
        open_i = src.index("{", m.end() - 1)
        depth, i = 0, open_i
        while True:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = src[open_i + 1:i]
        src = src[:m.start()] + src[i + 1:]
        body = re.sub(r"\s*SPIRV_Cross_Output stage_output;", "", body)
        body = re.sub(r"\s*return stage_output;", "", body)
        body = body.replace("stage_input.", "_in_").replace("stage_output.", "_out_")
        self.main_body = fix_big_literals(body)

        # function parameters: (Texture2D<T> x, SamplerState _x_sampler) -> sampler2D x
        src = re.sub(r"Texture2D<\w+>\s+(\w+)\s*,\s*SamplerState\s+_\1_sampler", r"sampler2D \1", src)
        src = re.sub(r"Texture2D<\w+>\s+(\w+)", r"sampler2D \1", src)
        src = _rewrite_texture_methods(src)
        # call arguments: f(tex, _tex_sampler, ...) -> f(tex, ...)
        src = re.sub(r"(\w+)\s*,\s*_\1_sampler\b", r"\1", src)
        if re.search(r"\b_\w+_sampler\b", src):
            raise XlateError("could not resolve separate sampler usage")
        src = fix_big_literals(src)
        src = flatten_2d_arrays(src.strip() + "\n")
        self.body = expand_struct_initializers(src)


def rename_reserved(code):
    return re.sub(r"\b[A-Za-z_]\w*\b",
                  lambda m: m.group(0) + "_rs" if m.group(0) in _RESHADE_RESERVED else m.group(0), code)


def replace_identifiers(code, mapping):
    if not mapping:
        return code
    pat = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)) + r")\b")
    return pat.sub(lambda m: mapping[m.group(1)], code)


def _match_bracket(s, i):
    """s[i] == '[' -> index of matching ']'."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "[":
            depth += 1
        elif s[j] == "]":
            depth -= 1
            if depth == 0:
                return j
    raise XlateError("unbalanced brackets")


def _match_brace(s, i):
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    raise XlateError("unbalanced braces")


def flatten_2d_arrays(src):
    """ReShade FX has no multi-dimensional arrays: rewrite T a[A][B]... into T a[A*B*...]."""
    decl = re.compile(r"\b(\w+)\s+(\w+)((?:\[\d+\]){2,})")
    dims = {}
    for m in decl.finditer(src):
        dims[m.group(2)] = [int(x) for x in re.findall(r"\d+", m.group(3))]
    if not dims:
        return src

    def flat_init(text, shape):
        text = text.strip()
        if not shape:
            return [text]
        if text.startswith("{"):
            elems = _split_args(text[1:_match_brace(text, 0)])
            res = []
            for e in elems:
                res += flat_init(e, shape[1:])
            return res
        # an array-valued expression (e.g. a temporary): index it
        res = []
        for j in range(shape[0]):
            res += flat_init(f"{text}[{j}]", shape[1:]) if len(shape) > 1 else [f"{text}[{j}]"]
        return res

    out, pos = "", 0
    for m in decl.finditer(src):
        shape = dims[m.group(2)]
        total = 1
        for d in shape:
            total *= d
        out += src[pos:m.start()] + f"{m.group(1)} {m.group(2)}[{total}]"
        pos = m.end()
        mi = re.match(r"\s*=\s*\{", src[pos:])
        if mi:
            ob = pos + mi.end() - 1
            cb = _match_brace(src, ob)
            elems = _split_args(src[ob + 1:cb])
            flat = []
            for e in elems:
                flat += flat_init(e, shape[1:])
            out += src[pos:ob] + "{ " + ", ".join(flat) + " }"
            pos = cb + 1
    src = out + src[pos:]

    pat = re.compile(r"\b(" + "|".join(map(re.escape, dims)) + r")\[")
    out, pos = "", 0
    while True:
        m = pat.search(src, pos)
        if not m:
            break
        shape = dims[m.group(1)]
        idx, j = [], m.end() - 1
        while len(idx) < len(shape) and j < len(src) and src[j] == "[":
            c = _match_bracket(src, j)
            inner = src[j + 1:c]
            idx.append(flatten_2d_arrays(inner) if "[" in inner else inner)
            j = c + 1
        if len(idx) < 2:
            out += src[pos:m.end()]
            pos = m.end()
            continue
        expr = f"({idx[0]})"
        for k in range(1, len(idx)):
            expr = f"({expr}) * {shape[k]} + ({idx[k]})"
        if len(idx) < len(shape):
            # partial indexing (row access) is not representable; let the compiler complain
            raise XlateError("partial indexing of a multi-dimensional array")
        out += src[pos:m.start()] + f"{m.group(1)}[{expr}]"
        pos = j
    return out + src[pos:]


def fix_big_literals(src):
    """ReShade's lexer chokes on very long float literals like 100000002004087734272.0f."""
    def rep(m):
        try:
            return f"{float(m.group(1)):.9e}f"
        except ValueError:
            return m.group(0)
    return re.sub(r"(?<![\w.])(\d{16,}\.\d*(?:[eE][+-]?\d+)?)f?", rep, src)


def _struct_defs(src):
    defs = {}
    for m in re.finditer(r"^struct\s+(\w+)\s*\{(.*?)\};", src, re.M | re.S):
        members = []
        for line in m.group(2).split(";"):
            mm = re.match(r"\s*(?:\w+\s+)*?(\w+)\s+(\w+)(?:\[(\d+)\])?\s*$", line)
            if mm:
                members.append((mm.group(1), mm.group(2), int(mm.group(3)) if mm.group(3) else 0))
        defs[m.group(1)] = members
    return defs


def _assign_init(target, typ, init, defs, out):
    """Emit statements that assign brace-initializer `init` to `target`."""
    init = init.strip()
    if typ in defs and init.startswith("{"):
        elems = _split_args(init[1:_match_brace(init, 0)])
        for (mt, mn, arr), e in zip(defs[typ], elems):
            if arr:
                e = e.strip()
                if e.startswith("{"):
                    for j, ee in enumerate(_split_args(e[1:_match_brace(e, 0)])):
                        _assign_init(f"{target}.{mn}[{j}]", mt, ee, defs, out)
                else:
                    for j in range(arr):
                        out.append(f"{target}.{mn}[{j}] = {e}[{j}];")
            else:
                _assign_init(f"{target}.{mn}", mt, e, defs, out)
    else:
        out.append(f"{target} = {init};")


def expand_struct_initializers(src):
    """`[static const] S x = { ... };` is not valid ReShade FX. Replace it with a
    generated constructor function."""
    defs = _struct_defs(src)
    if not defs:
        return src
    pat = re.compile(r"^([ \t]*)((?:static\s+)?(?:const\s+)?)(" + "|".join(map(re.escape, defs)) +
                     r")\s+(\w+)\s*=\s*\{", re.M)
    funcs = []
    counter = [0]
    while True:
        m = pat.search(src)
        if not m:
            break
        ob = m.end() - 1
        cb = _match_brace(src, ob)
        end = src.index(";", cb) + 1
        typ, name = m.group(3), m.group(4)
        counter[0] += 1
        fname = f"_sfx_make_{typ}_{counter[0]}"
        body = []
        _assign_init("r", typ, src[ob:cb + 1], defs, body)
        funcs.append((fname, typ, body, m.start()))
        is_global = m.group(1) == "" and "static" in m.group(2)
        if is_global:
            # global constant -> macro-like function call at each use
            src = src[:m.start()] + src[end:]
            src = re.sub(r"\b" + re.escape(name) + r"\b", f"{fname}()", src)
        else:
            # local: declare, then assign member by member right here
            funcs.pop()
            ind = m.group(1)
            stmts = []
            _assign_init(name, typ, src[ob:cb + 1], defs, stmts)
            src = src[:m.start()] + f"{ind}{typ} {name};\n" + "".join(f"{ind}{st}\n" for st in stmts) + src[end:]
    if not funcs:
        return src
    # insert constructor functions after the last struct definition they depend on
    last_struct = max(mm.end() for mm in re.finditer(r"^struct\s+\w+\s*\{.*?\};", src, re.M | re.S))
    text = ""
    for fname, typ, body, _ in funcs:
        text += f"\n{typ} {fname}()\n{{\n    {typ} r;\n" + "".join(f"    {b}\n" for b in body) + "    return r;\n}\n"
    return src[:last_struct] + text + src[last_struct:]
