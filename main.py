import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import decky

PLUGIN_DIR = decky.DECKY_PLUGIN_DIR
sys.path.insert(0, PLUGIN_DIR)

from slangfx import convert, Options, ConvertError  # noqa: E402
from slangfx import build as slangfx_build  # noqa: E402

logger = decky.logger

RESHADE_DIR = os.path.join(decky.DECKY_USER_HOME, ".local/share/gamescope/reshade")
SHADERS_DIR = os.path.join(RESHADE_DIR, "Shaders")
TEXTURES_DIR = os.path.join(RESHADE_DIR, "Textures")
FX_PREFIX = "deckglass_"
LUT_ROOT = "deckglass"

SETTINGS_DIR = decky.DECKY_PLUGIN_SETTINGS_DIR
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")
CACHE_DIR = os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "cache")
# Crash guard: exists while a freshly set shader has not yet proven stable.
GUARD_FILE = os.path.join(SETTINGS_DIR, "pending_shader.json")
GUARD_SECONDS = 8
CRASH_MESSAGE = ("gamescope ist mit diesem Shader abgestürzt. Der Shader wurde deaktiviert. "
                 "Bitte ein anderes Preset wählen oder die Logs melden.")

# Shader libraries: bundled ones plus a user folder for own presets.
# Layout mirrors RetroArch, so Retro Crisis' "../../../shaders_slang/..." paths resolve.
USER_LIBRARY = os.path.join(decky.DECKY_USER_HOME, "deckglass-shaders")
LIBRARIES = [
    ("Retro Crisis", os.path.join(PLUGIN_DIR, "shaders", "rc", "retro crisis")),
    ("RetroArch", os.path.join(PLUGIN_DIR, "shaders", "shaders_slang")),
    ("Eigene", USER_LIBRARY),
]

# Native resolutions of common systems. The game image is assumed to be scaled to
# fit the screen with its aspect ratio kept (no integer scaling).
SYSTEMS = [
    {"id": "nes", "label": "NES / Famicom", "w": 256, "h": 240},
    {"id": "snes", "label": "SNES", "w": 256, "h": 224},
    {"id": "md", "label": "Mega Drive / Genesis", "w": 320, "h": 224},
    {"id": "sms", "label": "Master System", "w": 256, "h": 192},
    {"id": "pce", "label": "PC Engine", "w": 256, "h": 232},
    {"id": "n64", "label": "Nintendo 64", "w": 320, "h": 240},
    {"id": "psx", "label": "PlayStation", "w": 320, "h": 240},
    {"id": "psx480", "label": "PlayStation (480i)", "w": 640, "h": 480},
    {"id": "saturn", "label": "Saturn", "w": 320, "h": 224},
    {"id": "dc", "label": "Dreamcast", "w": 640, "h": 480},
    {"id": "ps2", "label": "PlayStation 2", "w": 640, "h": 448},
    {"id": "gc", "label": "GameCube / Wii", "w": 640, "h": 480},
    {"id": "arcade", "label": "Arcade (CPS)", "w": 384, "h": 224},
    {"id": "gb", "label": "Game Boy / Color", "w": 160, "h": 144},
    {"id": "gba", "label": "Game Boy Advance", "w": 240, "h": 160},
    {"id": "nds", "label": "Nintendo DS (ein Bildschirm)", "w": 256, "h": 192},
    {"id": "psp", "label": "PSP", "w": 480, "h": 272},
    {"id": "gg", "label": "Game Gear", "w": 160, "h": 144},
    {"id": "ngp", "label": "Neo Geo Pocket", "w": 160, "h": 152},
    {"id": "ws", "label": "WonderSwan", "w": 224, "h": 144},
]
SYSTEM_BY_ID = {s["id"]: s for s in SYSTEMS}

DEFAULT_SETTINGS = {
    "enabled": False,
    "preset": None,          # "<library>/<relative path>"
    "scale_mode": "system",  # "system" | "manual"
    "system": "snes",
    "pixel_size": 3.0,
    "params": {},            # preset key -> {name: value}
    "only_in_game": True,    # keep the Steam UI untouched
}


def _xprop_set(value):
    env = dict(os.environ)
    env["DISPLAY"] = ":0"
    env["LD_LIBRARY_PATH"] = ""
    if value:
        cmd = ["xprop", "-root", "-f", "GAMESCOPE_RESHADE_EFFECT", "8u", "-set", "GAMESCOPE_RESHADE_EFFECT", value]
    else:
        cmd = ["xprop", "-root", "-remove", "GAMESCOPE_RESHADE_EFFECT"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.error(f"xprop failed: {e}")
        return False
    if r.returncode != 0:
        logger.error(f"xprop failed: {r.stderr.strip()}")
        return False
    return True


def _gamescope_pids():
    pids = set()
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/comm") as f:
                if f.read().strip() in ("gamescope", "gamescope-wl"):
                    pids.add(int(d))
        except OSError:
            pass
    return pids


def _write_guard(fx, pids):
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    with open(GUARD_FILE, "w") as f:
        json.dump({"fx": fx, "pids": sorted(pids)}, f)


def _clear_guard():
    try:
        os.remove(GUARD_FILE)
    except FileNotFoundError:
        pass


class Plugin:
    settings = dict(DEFAULT_SETTINGS)
    current_fx = None
    lock = None
    last_error = None
    in_game = False
    guard_task = None

    # ------------------------------------------------------------------ settings
    @staticmethod
    async def _load():
        try:
            with open(SETTINGS_FILE) as f:
                data = json.load(f)
            s = dict(DEFAULT_SETTINGS)
            s.update(data)
            Plugin.settings = s
        except FileNotFoundError:
            Plugin.settings = dict(DEFAULT_SETTINGS)
        except Exception:
            logger.exception("could not read settings")
            Plugin.settings = dict(DEFAULT_SETTINGS)

    @staticmethod
    def _save():
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(Plugin.settings, f, indent=1)
        os.replace(tmp, SETTINGS_FILE)

    # ------------------------------------------------------------------ library
    @staticmethod
    def _resolve_preset(key):
        if not key or "/" not in key:
            return None
        lib, rel = key.split("/", 1)
        for name, root in LIBRARIES:
            if name == lib:
                path = os.path.normpath(os.path.join(root, rel))
                if path.startswith(os.path.normpath(root) + os.sep) and os.path.isfile(path):
                    return path
        return None

    async def get_library(self):
        """All presets grouped by library and folder."""
        os.makedirs(USER_LIBRARY, exist_ok=True)
        result = []
        for name, root in LIBRARIES:
            if not os.path.isdir(root):
                continue
            groups = {}
            for p in sorted(Path(root).rglob("*.slangp")):
                if "__MACOSX" in p.parts or p.name.startswith("._"):
                    continue
                rel = p.relative_to(root).as_posix()
                folder = os.path.dirname(rel) or "."
                groups.setdefault(folder, []).append({"key": f"{name}/{rel}", "label": p.stem})
            if groups:
                result.append({"library": name, "groups": [{"folder": k, "presets": v} for k, v in sorted(groups.items())]})
        return result

    async def get_systems(self):
        return SYSTEMS

    async def get_settings(self):
        return {**Plugin.settings, "last_error": Plugin.last_error}

    # ------------------------------------------------------------------ apply
    @staticmethod
    def _options(key):
        s = Plugin.settings
        params = s.get("params", {}).get(key, {})
        if s.get("scale_mode") == "manual":
            return Options(pixel_size=float(s.get("pixel_size") or 1.0), params=params)
        sysd = SYSTEM_BY_ID.get(s.get("system"), SYSTEM_BY_ID["snes"])
        return Options(native=(sysd["w"], sysd["h"]), params=params)

    @staticmethod
    def _build(key):
        path = Plugin._resolve_preset(key)
        if not path:
            raise ConvertError("Preset nicht gefunden")
        opts = Plugin._options(key)
        tag = hashlib.sha1(key.encode()).hexdigest()[:10]
        opts.lut_dir = f"{LUT_ROOT}/{tag}"
        slangfx_build.CACHE_DIR = CACHE_DIR
        fx, luts, plist = convert(path, opts)
        digest = hashlib.sha1(fx.encode()).hexdigest()[:12]
        os.makedirs(SHADERS_DIR, exist_ok=True)
        name = f"{FX_PREFIX}{digest}.fx"
        with open(os.path.join(SHADERS_DIR, name), "w") as f:
            f.write(fx)
        for src, rel in luts:
            dst = os.path.join(TEXTURES_DIR, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(src):
                shutil.copyfile(src, dst)
        return name, plist

    @staticmethod
    def _cleanup(keep):
        for p in Path(SHADERS_DIR).glob(f"{FX_PREFIX}*.fx"):
            if p.name != keep:
                try:
                    p.unlink()
                except OSError:
                    pass

    @staticmethod
    async def _apply():
        """Convert the selected preset (if enabled) and hand it to gamescope."""
        s = Plugin.settings
        if not s.get("enabled") or not s.get("preset") or (s.get("only_in_game") and not Plugin.in_game):
            _xprop_set(None)
            Plugin.current_fx = None
            _clear_guard()
            plist = []
            path = Plugin._resolve_preset(s.get("preset"))
            if path:
                try:
                    plist = await asyncio.to_thread(slangfx_build.list_parameters, path,
                                                    s.get("params", {}).get(s["preset"], {}))
                except Exception:
                    logger.exception("list parameters")
            return {"ok": True, "params": plist, "warning": Plugin.last_error}
        key = s["preset"]
        try:
            name, plist = await asyncio.to_thread(Plugin._build, key)
        except ConvertError as e:
            Plugin.last_error = str(e)
            logger.error(f"convert {key}: {e}")
            return {"ok": False, "error": str(e), "params": []}
        except Exception as e:
            Plugin.last_error = f"{type(e).__name__}: {e}"
            logger.exception(f"convert {key}")
            return {"ok": False, "error": Plugin.last_error, "params": []}
        if name != Plugin.current_fx:
            pids = _gamescope_pids()
            _write_guard(name, pids)
            _xprop_set(name)
            if Plugin.guard_task:
                Plugin.guard_task.cancel()
            Plugin.guard_task = asyncio.create_task(Plugin._watch_guard(name, pids))
        Plugin.current_fx = name
        Plugin.last_error = None
        await asyncio.to_thread(Plugin._cleanup, name)
        return {"ok": True, "params": plist}

    @staticmethod
    async def _watch_guard(fx, pids_before):
        """If gamescope dies right after a shader was set, disable the shader."""
        try:
            await asyncio.sleep(GUARD_SECONDS)
        except asyncio.CancelledError:
            return
        if pids_before and not pids_before <= _gamescope_pids():
            logger.error(f"gamescope restarted after setting {fx}; disabling shader")
            async with Plugin.lock:
                Plugin.settings["enabled"] = False
                Plugin._save()
                Plugin.last_error = CRASH_MESSAGE
                Plugin.current_fx = None
                _xprop_set(None)
        _clear_guard()

    async def update_settings(self, changes):
        """Merge changes from the UI, save and re-apply."""
        async with Plugin.lock:
            if changes.get("enabled") or "preset" in changes:
                Plugin.last_error = None
            for k in ("enabled", "preset", "scale_mode", "system", "pixel_size", "only_in_game"):
                if k in changes:
                    Plugin.settings[k] = changes[k]
            Plugin._save()
            return await Plugin._apply()

    async def set_param(self, preset, name, value):
        async with Plugin.lock:
            Plugin.settings.setdefault("params", {}).setdefault(preset, {})[name] = float(value)
            Plugin._save()
            return await Plugin._apply()

    async def reset_params(self, preset):
        async with Plugin.lock:
            Plugin.settings.setdefault("params", {}).pop(preset, None)
            Plugin._save()
            return await Plugin._apply()

    async def set_in_game(self, running):
        async with Plugin.lock:
            if bool(running) == Plugin.in_game:
                return {"ok": True, "params": []}
            Plugin.in_game = bool(running)
            return await Plugin._apply()

    async def reapply(self):
        async with Plugin.lock:
            return await Plugin._apply()

    # ------------------------------------------------------------------ lifecycle
    async def _main(self):
        Plugin.lock = asyncio.Lock()
        os.makedirs(USER_LIBRARY, exist_ok=True)
        for tool in ("glslang", "spirv-cross"):
            p = os.path.join(PLUGIN_DIR, "slangfx", "bin", tool)
            try:
                os.chmod(p, 0o755)
            except OSError:
                pass
        await Plugin._load()
        if os.path.exists(GUARD_FILE):
            logger.error("previous shader did not survive; starting with shader disabled")
            Plugin.settings["enabled"] = False
            Plugin._save()
            Plugin.last_error = CRASH_MESSAGE
            _clear_guard()
        async with Plugin.lock:
            await Plugin._apply()
        logger.info("DeckGlass ready")

    async def _unload(self):
        _xprop_set(None)

    async def _uninstall(self):
        _xprop_set(None)
        for p in Path(SHADERS_DIR).glob(f"{FX_PREFIX}*.fx"):
            p.unlink(missing_ok=True)
        shutil.rmtree(os.path.join(TEXTURES_DIR, LUT_ROOT), ignore_errors=True)
