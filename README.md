# DeckGlass

> **Beta:** works well in testing, but expect rough edges. Please report problems via GitHub issues.

A Decky plugin that puts **RetroArch shaders (slang presets)** on top of the whole game image on the Steam Deck – for every game and emulator, not just inside RetroArch. Inspired by [ShaderGlass](https://github.com/mausimus/ShaderGlass), built on [Reshadeck](https://github.com/safijari/Reshadeck).

## How it works

gamescope can apply ReShade effects (`.fx`) to the image of the running game. DeckGlass converts RetroArch presets (`.slangp`) into such an effect, right on the Deck:

```
.slangp/.slang ─► glslang ─► SPIR-V ─► SPIRV-Cross (HLSL) ─► slangfx ─► one ReShade .fx
```

- Multiple passes, aliases, feedback, `OriginalHistory`, LUT textures and parameters are supported.
- **Source resolution:** the first pass scales the game image down by a pixel size, so scanlines and masks line up with the game's lines. Pick the system, then set **Black bars top & bottom**: off if the emulator fills the screen height (800 lines), on if it shows a 16:9 area with bars (720 lines). Pixel sizes are round (e.g. SNES 4× / 3×, PS2 2× / 1.5×), because round values keep every scanline equally thick. Both choices can be stored per game; a manual pixel size is available too.
- Every change writes a new `.fx` that gamescope loads immediately.

## Shaders

Presets are picked in a file browser, like in RetroArch, with two top folders:

- **RetroArch:** the [libretro slang-shaders](https://github.com/libretro/slang-shaders). Mega Bezel, HDR shaders, test patterns and source-only folders are hidden.
- **RetroCrisis:** the [Retro Crisis GDV-NTSC](https://github.com/RetroCrisis/Retro-Crisis-GDV-NTSC) presets (GPL-3), including the "720p Steam Deck" folder.

## Usage

- **Enable shader / Only in games:** "Only in games" keeps the Steam menus untouched. If it is off, the shader starts disabled after a restart.
- **Per-game settings:** while a game is running, this switch stores all settings for that game only. When the game quits, the global settings apply again.
- **Parameters:** a separate dialog with all sliders of the preset.
- **OLED defringe (Deck OLED only):** the subpixel layout of some Deck OLED screens (Samsung panel) causes slight colour fringing that a small number of people notice. This switch adds a small correction pass – taken from Reshadeck's `DefringSamsungOLEDeck.fx` – on top of any shader, or on its own. It is hidden on the LCD model and off by default.
- **Crash guard:** if gamescope crashes right after a shader is applied, the shader is turned off – also across restarts.

## Limitations

- CRT masks (aperture grille, slot mask) are designed for RGB-stripe LCDs and can look tinted on the Deck OLED, just like in RetroArch. Pick a different mask type in the preset parameters if that happens.
- Screenshots do not include the shader.
- Heavy presets (e.g. crt-royale) cost a lot of GPU time.
- A few presets cannot be converted (e.g. matrix ternaries); the error is shown in the plugin.
- gamescope does not support mipmaps, so `mipmap_input` is ignored.

## Releases

Raising `version` in `package.json` on `main` (together with `release-notes/<version>.md`) makes GitHub Actions build the zip and publish "Version <version>"; 0.x versions are marked as pre-releases.

## Building

```bash
pnpm install
./package.sh /path/to/slang-shaders [/path/to/unpacked/retro-crisis-release]
```

Result: `out/DeckGlass-<version>.zip`, installable via Decky (developer mode → "Install Plugin from ZIP").

## Disclaimer

Not affiliated with Valve, libretro or Retro Crisis. "Steam" and "Steam Deck" are trademarks of Valve Corporation.

## License

GPL-3.0 (see `LICENSE`), like Reshadeck. Bundled tools: glslang and SPIRV-Cross (licenses in `slangfx/bin/licenses/`). The shaders are licensed by their respective authors.
