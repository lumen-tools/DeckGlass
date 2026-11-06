# DeckGlass

> **Early release (0.x):** works well in testing, but not 1.0 yet – expect rough edges and please report problems via GitHub issues.

A Decky plugin that puts **RetroArch shaders (slang presets)** on top of the whole game image on the Steam Deck – for every game and emulator, not just inside RetroArch. Inspired by [ShaderGlass](https://github.com/mausimus/ShaderGlass), built on [Reshadeck](https://github.com/safijari/Reshadeck).

## How it works

gamescope can apply ReShade effects (`.fx`) to the image of the running game. DeckGlass converts RetroArch presets (`.slangp`) into such an effect, right on the Deck:

```
.slangp/.slang ─► glslang ─► SPIR-V ─► SPIRV-Cross (HLSL) ─► slangfx ─► one ReShade .fx
```

- Multiple passes, aliases, feedback, `OriginalHistory`, LUT textures and parameters are supported.
- **Source resolution:** the first pass scales the game image down to the source resolution the shader works with. Choose **240p** (NES, SNES, Mega Drive, PS1, N64, Saturn, Arcade), **480i/p** (PS2, GameCube, Wii, Dreamcast) or a handheld, then **720p** or **800p** values (240p: 3× / 3.3×, 480i/p: 1.5× / 1.7×). Try both and see what looks better for each game and shader – 720p values usually give the most even scanlines. A manual pixel size is available too.
- **Bilinear filtering** (on by default): smooths the game image while it is scaled down to the source resolution – this usually looks closer to RetroArch. Can be turned off, also per game.
- Every change writes a new `.fx` that gamescope loads immediately.

## Shaders

Presets are picked in a file browser, like in RetroArch, with three top folders – **RetroArch**, **Retro_Crisis** and **Custom_Shaders**:

- **RetroArch:** the [libretro slang-shaders](https://github.com/libretro/slang-shaders). Mega Bezel, HDR shaders, test patterns and source-only folders are hidden.
- **Retro_Crisis:** the [Retro Crisis GDV-NTSC](https://github.com/RetroCrisis/Retro-Crisis-GDV-NTSC) presets (GPL-3), including the "720p Steam Deck" folder.
- **Custom_Shaders:** your own presets. Copy them – with their folders – to `~/DeckGlass/Custom_Shaders` (e.g. in desktop mode), then reopen the browser. Presets that point to RetroArch's shader folder (`shaders_slang/...`), such as presets saved in RetroArch or a newer Retro Crisis release, find those files in the bundled libretro shaders automatically.

## Usage

- **Enable shader / Only in games:** "Only in games" keeps the Steam menus untouched. If it is off, the shader starts disabled after a restart.
- **Per-game settings:** while a game is running, this switch stores all settings for that game only. When the game quits, the global settings apply again.
- **Parameters:** a separate dialog with all sliders of the preset; changes are applied when you press Apply or close it. Changes stay as long as you keep the shader – also across restarts – and reset when you choose another shader or save state.
- **Save states:** store the shader, source resolution and all parameters under a name (e.g. `retro-crisis-ps2-dirty-1.5x`), load them from a dropdown, replace or delete them. Per-game settings remember the loaded save state.
- **OLED defringe (Deck OLED only):** the subpixel layout of some Deck OLED screens (Samsung panel) causes slight colour fringing that a small number of people notice. This switch adds a small correction pass – taken from Reshadeck's `DefringSamsungOLEDeck.fx` – on top of any shader, or on its own. It is hidden on the LCD model and off by default.
- **Crash guard:** if gamescope crashes right after a shader is applied, the shader is turned off and a warning is shown. A normal restart of the Deck is not treated as a crash.

## Limitations

- Whenever the active shader changes in a game (turned on, another preset, new parameters …), gamescope compiles it and the whole picture pauses until it is done – a moment for small shaders, longer for big ones like Retro Crisis. DeckGlass shows a notice the first time this happens for a shader. Best set the shader up before starting the game.
- CRT masks (aperture grille, slot mask) are designed for RGB-stripe LCDs and can look tinted on the Deck OLED, just like in RetroArch. Pick a different mask type in the preset parameters if that happens.
- Screenshots do not include the shader.
- Heavy presets (e.g. crt-royale) cost a lot of GPU time.
- A few presets cannot be converted (e.g. matrix ternaries); the error is shown in the plugin.
- gamescope does not support mipmaps; DeckGlass emulates the mip levels that shaders read explicitly (e.g. crt-geom-deluxe's average brightness).

## Releases

Raising `version` in `package.json` on `main` (together with `release-notes/<version>.md`) makes GitHub Actions build the zip and publish "Version <version>". Versions with a suffix (e.g. `0.3.0-beta`) are marked as pre-releases.

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
