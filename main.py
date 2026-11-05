import asyncio
import copy
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
CRASH_MESSAGE = ("gamescope crashed with this shader, so it was turned off. "
                 "Please pick another preset or report the logs.")

# Shader libraries: bundled ones plus a user folder for own presets.
# Layout mirrors RetroArch, so Retro Crisis' "../../../shaders_slang/..." paths resolve.
LIBRARIES = [
    ("RetroArch", os.path.join(PLUGIN_DIR, "shaders", "shaders_slang")),
    ("RetroCrisis", os.path.join(PLUGIN_DIR, "shaders", "rc", "retro crisis")),
]

# Native resolutions of common systems, with the pixel size used for them. The exact
# fit on the 1280x800 screen is rarely a round number (SNES: 800/224 = 3.57); a round
# pixel size keeps every scanline equally thick, which looks much calmer than an exact
# but fractional one. Values are the nearest of 1.5, 2, 2.5, 3, 4, 5, 6.
SYSTEMS = [
    {"id": "nes", "label": "NES / Famicom", "w": 256, "h": 240, "px": 3},
    {"id": "snes", "label": "SNES", "w": 256, "h": 224, "px": 4},
    {"id": "md", "label": "Mega Drive / Genesis", "w": 320, "h": 224, "px": 4},
    {"id": "sms", "label": "Master System", "w": 256, "h": 192, "px": 4},
    {"id": "pce", "label": "PC Engine", "w": 256, "h": 232, "px": 3},
    {"id": "n64", "label": "Nintendo 64", "w": 320, "h": 240, "px": 3},
    {"id": "psx", "label": "PlayStation", "w": 320, "h": 240, "px": 3},
    {"id": "psx480", "label": "PlayStation (480i)", "w": 640, "h": 480, "px": 1.5},
    {"id": "saturn", "label": "Saturn", "w": 320, "h": 224, "px": 4},
    {"id": "dc", "label": "Dreamcast", "w": 640, "h": 480, "px": 1.5},
    {"id": "ps2", "label": "PlayStation 2", "w": 640, "h": 448, "px": 2},
    {"id": "gc", "label": "GameCube / Wii", "w": 640, "h": 480, "px": 1.5},
    {"id": "arcade", "label": "Arcade (CPS)", "w": 384, "h": 224, "px": 3},
    {"id": "gb", "label": "Game Boy / Color", "w": 160, "h": 144, "px": 6},
    {"id": "gba", "label": "Game Boy Advance", "w": 240, "h": 160, "px": 5},
    {"id": "nds", "label": "Nintendo DS (one screen)", "w": 256, "h": 192, "px": 4},
    {"id": "psp", "label": "PSP", "w": 480, "h": 272, "px": 2.5},
    {"id": "gg", "label": "Game Gear", "w": 160, "h": 144, "px": 6},
    {"id": "ngp", "label": "Neo Geo Pocket", "w": 160, "h": 152, "px": 5},
    {"id": "ws", "label": "WonderSwan", "w": 224, "h": 144, "px": 6},
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
    "oled_defringe": False,  # Deck OLED only: reduce colour fringing (device setting, not per game)
    "profiles": {},          # Steam app id -> per-game copy of PROFILE_KEYS (+ "name")
}
# Settings that a per-game profile overrides. "only_in_game" stays global.
PROFILE_KEYS = ("enabled", "preset", "scale_mode", "system", "pixel_size", "params")


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


# Folders that only hold shader sources, old variants or need HDR output.
HIDDEN_DIRS = {"shaders", "shader", "old", "hooks", "resources", "include", "includes", "hdr", "bezel", "test",
               "__macosx"}


def _tidy_labels(names):
    """Strip a prefix shared by every preset in a folder, e.g. "RC GDV-NTSC - "."""
    if len(names) < 2:
        return {n: n for n in names}
    prefix = os.path.commonprefix(names)
    cut = prefix.rfind(" - ")
    if cut <= 0:
        return {n: n for n in names}
    cut += 3
    return {n: (n[cut:].strip() or n) for n in names}


def _scan(lib, root, rel=""):
    path = os.path.join(root, rel)
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
    except OSError:
        return None
    folders, stems = [], []
    for e in entries:
        if e.name.startswith(".") or e.name.startswith("._"):
            continue
        if e.is_dir():
            if e.name.lower() in HIDDEN_DIRS:
                continue
            sub = _scan(lib, root, os.path.join(rel, e.name) if rel else e.name)
            if sub:
                folders.append(sub)
        elif e.name.lower().endswith(".slangp"):
            stems.append(e.name[:-7])
    if not folders and not stems:
        return None
    labels = _tidy_labels(stems)
    presets = [{"key": f"{lib}/{rel + '/' if rel else ''}{st}.slangp", "label": labels[st]} for st in stems]
    presets.sort(key=lambda p: p["label"].lower())
    return {"name": os.path.basename(rel) if rel else lib, "folders": folders, "presets": presets}


def _build_library():
    libs = []
    for name, root in LIBRARIES:
        node = _scan(name, root) if os.path.isdir(root) else None
        if node is None:
            node = {"name": name, "folders": [], "presets": []}
        libs.append(node)
    return {"name": "", "folders": libs, "presets": []}


def _write_fx(fx):
    digest = hashlib.sha1(fx.encode()).hexdigest()[:12]
    os.makedirs(SHADERS_DIR, exist_ok=True)
    name = f"{FX_PREFIX}{digest}.fx"
    with open(os.path.join(SHADERS_DIR, name), "w") as f:
        f.write(fx)
    return name


def _is_oled():
    """Steam Deck OLED reports "Galileo", the LCD model "Jupiter"."""
    try:
        with open("/sys/class/dmi/id/product_name") as f:
            return f.read().strip() == "Galileo"
    except OSError:
        return False


IS_OLED = _is_oled()


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


def _write_guard(fx, pids, app_id=None):
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    with open(GUARD_FILE, "w") as f:
        json.dump({"fx": fx, "pids": sorted(pids), "app_id": app_id}, f)


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
    app_id = None      # Steam app id of the running game (string)
    app_name = None
    guard_task = None

    @staticmethod
    def _active():
        """The settings in effect: the running game's profile, or the global settings."""
        prof = Plugin.settings.get("profiles", {}).get(Plugin.app_id) if Plugin.app_id else None
        return prof if prof is not None else Plugin.settings

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
        """Preset tree for the file browser: libraries are the top-level folders."""
        return await asyncio.to_thread(_build_library)

    async def get_params(self):
        """Parameters of the active preset with their current values."""
        a = Plugin._active()
        path = Plugin._resolve_preset(a.get("preset"))
        if not path:
            return []
        try:
            return await asyncio.to_thread(slangfx_build.list_parameters, path,
                                           a.get("params", {}).get(a["preset"], {}))
        except Exception:
            logger.exception("list parameters")
            return []

    async def get_systems(self):
        return SYSTEMS

    async def get_settings(self):
        a = Plugin._active()
        res = {k: a.get(k, DEFAULT_SETTINGS[k]) for k in PROFILE_KEYS if k != "params"}
        res.update({
            "only_in_game": Plugin.settings.get("only_in_game", True),
            "is_oled": IS_OLED,
            "oled_defringe": bool(Plugin.settings.get("oled_defringe")),
            "last_error": Plugin.last_error,
            "app_id": Plugin.app_id,
            "app_name": Plugin.app_name,
            "profile_active": a is not Plugin.settings,
        })
        return res

    # ------------------------------------------------------------------ apply
    @staticmethod
    def _options(key):
        s = Plugin._active()
        params = s.get("params", {}).get(key, {})
        if s.get("scale_mode") == "manual":
            return Options(pixel_size=float(s.get("pixel_size") or 1.0), params=params)
        sysd = SYSTEM_BY_ID.get(s.get("system"), SYSTEM_BY_ID["snes"])
        return Options(pixel_size=float(sysd["px"]), params=params)

    @staticmethod
    def _defringe():
        return IS_OLED and bool(Plugin.settings.get("oled_defringe"))

    @staticmethod
    def _build(key):
        path = Plugin._resolve_preset(key)
        if not path:
            raise ConvertError("Preset not found")
        opts = Plugin._options(key)
        opts.defringe = Plugin._defringe()
        tag = hashlib.sha1(key.encode()).hexdigest()[:10]
        opts.lut_dir = f"{LUT_ROOT}/{tag}"
        slangfx_build.CACHE_DIR = CACHE_DIR
        fx, luts, plist = convert(path, opts)
        name = _write_fx(fx)
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
        s = Plugin._active()
        only_in_game = Plugin.settings.get("only_in_game", True)
        if not s.get("enabled") or not s.get("preset") or (only_in_game and not Plugin.in_game):
            if Plugin._defringe():
                # no retro shader right now, but the OLED defringe still applies everywhere
                name = _write_fx(slangfx_build.defringe_only_fx())
                if name != Plugin.current_fx:
                    _xprop_set(name)
                    Plugin.current_fx = name
                await asyncio.to_thread(Plugin._cleanup, name)
            else:
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
            Plugin.last_error = f"Conversion failed: {e}"
            logger.error(f"convert {key}: {e}")
            return {"ok": False, "error": Plugin.last_error, "params": []}
        except Exception as e:
            Plugin.last_error = f"Internal error ({type(e).__name__}): {e}"
            logger.exception(f"convert {key}")
            return {"ok": False, "error": Plugin.last_error, "params": []}
        if name != Plugin.current_fx:
            pids = _gamescope_pids()
            _write_guard(name, pids, Plugin.app_id if Plugin._active() is not Plugin.settings else None)
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
                Plugin._active()["enabled"] = False
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
            target = Plugin._active()
            for k in ("enabled", "preset", "scale_mode", "system", "pixel_size"):
                if k in changes:
                    target[k] = changes[k]
            if "oled_defringe" in changes:
                Plugin.settings["oled_defringe"] = bool(changes["oled_defringe"])
            if "only_in_game" in changes:
                Plugin.settings["only_in_game"] = bool(changes["only_in_game"])
            Plugin._save()
            return await Plugin._apply()

    async def set_param(self, preset, name, value):
        async with Plugin.lock:
            Plugin._active().setdefault("params", {}).setdefault(preset, {})[name] = float(value)
            Plugin._save()
            return await Plugin._apply()

    async def reset_params(self, preset):
        async with Plugin.lock:
            Plugin._active().setdefault("params", {}).pop(preset, None)
            Plugin._save()
            return await Plugin._apply()

    async def set_in_game(self, running, app_id=None, app_name=None):
        """Called by the frontend whenever the running game changes."""
        app_id = str(app_id) if running and app_id not in (None, "", 0, "0") else None
        async with Plugin.lock:
            if bool(running) == Plugin.in_game and app_id == Plugin.app_id:
                return {"ok": True, "params": []}
            Plugin.in_game = bool(running)
            Plugin.app_id = app_id
            Plugin.app_name = app_name if app_id else None
            return await Plugin._apply()

    async def set_profile(self, enabled):
        """Per-game switch: on = store the current settings for the running game."""
        async with Plugin.lock:
            if not Plugin.app_id:
                return {"ok": False, "error": "No game is running", "params": []}
            profiles = Plugin.settings.setdefault("profiles", {})
            if enabled:
                if Plugin.app_id not in profiles:
                    prof = {k: copy.deepcopy(Plugin.settings.get(k, DEFAULT_SETTINGS[k])) for k in PROFILE_KEYS}
                    prof["name"] = Plugin.app_name
                    profiles[Plugin.app_id] = prof
            else:
                profiles.pop(Plugin.app_id, None)
            Plugin._save()
            return await Plugin._apply()

    async def reapply(self):
        async with Plugin.lock:
            return await Plugin._apply()

    # ------------------------------------------------------------------ lifecycle
    async def _main(self):
        Plugin.lock = asyncio.Lock()
        for tool in ("glslang", "spirv-cross"):
            p = os.path.join(PLUGIN_DIR, "slangfx", "bin", tool)
            try:
                os.chmod(p, 0o755)
            except OSError:
                pass
        await Plugin._load()
        if os.path.exists(GUARD_FILE):
            logger.error("previous shader did not survive; starting with shader disabled")
            try:
                with open(GUARD_FILE) as f:
                    crashed_app = json.load(f).get("app_id")
            except Exception:
                crashed_app = None
            prof = Plugin.settings.get("profiles", {}).get(crashed_app) if crashed_app else None
            (prof if prof is not None else Plugin.settings)["enabled"] = False
            Plugin._save()
            Plugin.last_error = CRASH_MESSAGE
            _clear_guard()
        # A globally applied shader (also on the Steam UI) starts switched off after a
        # restart, so a too coarse setting can never lock the user out.
        if not Plugin.settings.get("only_in_game", True) and Plugin.settings.get("enabled"):
            Plugin.settings["enabled"] = False
            Plugin._save()
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
