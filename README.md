# SlangDeck

Decky-Plugin, das **RetroArch-Shader (Slang-Presets)** auf das gesamte Spielbild des Steam Deck legt – für jedes Spiel und jeden Emulator, nicht nur in RetroArch. Inspiriert von [ShaderGlass](https://github.com/mausimus/ShaderGlass), aufgebaut auf [Reshadeck](https://github.com/safijari/Reshadeck).

## Funktionsweise

gamescope kann ReShade-Effekte (`.fx`) auf das Bild des laufenden Spiels anwenden. SlangDeck wandelt RetroArch-Presets (`.slangp`) direkt auf dem Deck in einen solchen Effekt um:

```
.slangp/.slang ─► glslang ─► SPIR-V ─► SPIRV-Cross (HLSL) ─► slangfx ─► eine ReShade-.fx
```

- Mehrere Durchläufe, Aliase, Feedback, `OriginalHistory`, LUT-Texturen und Parameter werden unterstützt.
- **Quellauflösung:** Der erste Durchlauf rechnet das Spielbild auf die Originalauflösung des Systems herunter (z. B. PS2 640×448), damit Scanlines und Masken stimmen. Angenommen wird ein bildschirmfüllend skaliertes Bild (ohne Integer-Skalierung). Alternativ manuell über eine Pixelgröße.
- Parameter lassen sich im Plugin einstellen; jede Änderung erzeugt eine neue `.fx`, die gamescope sofort lädt.

## Shader

- **RetroArch:** die [libretro slang-shaders](https://github.com/libretro/slang-shaders) (ohne Mega Bezel).
- **Eigene:** beliebige Slang-Presets nach `~/slangdeck-shaders/` kopieren (Ordnerstruktur mit `#include`s und Texturen beibehalten).

## Einschränkungen

- Screenshots enthalten den Shader nicht.
- Schwere Presets (z. B. crt-royale) kosten viel GPU-Leistung.
- Ein kleiner Teil der Presets lässt sich nicht umwandeln (z. B. Matrix-Ternaries, HDR-Spezialfälle); der Fehler wird im Plugin angezeigt.

## Bauen

```bash
pnpm install
./package.sh /pfad/zu/slang-shaders [/pfad/zu/retro-crisis]
```

Ergebnis: `out/SlangDeck-<version>.zip`, installierbar über Decky (Entwicklermodus → „Install Plugin from ZIP“).

## Lizenz

GPL-3.0 (siehe `LICENSE`), wie Reshadeck. Mitgelieferte Werkzeuge: glslang und SPIRV-Cross (Lizenzen in `slangfx/bin/licenses/`). Die Shader stehen unter den Lizenzen ihrer jeweiligen Autoren.
