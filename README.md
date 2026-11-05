# DeckGlass

Decky-Plugin, das **RetroArch-Shader (Slang-Presets)** auf das gesamte Spielbild des Steam Deck legt – für jedes Spiel und jeden Emulator, nicht nur in RetroArch. Inspiriert von [ShaderGlass](https://github.com/mausimus/ShaderGlass), aufgebaut auf [Reshadeck](https://github.com/safijari/Reshadeck).

## Funktionsweise

gamescope kann ReShade-Effekte (`.fx`) auf das Bild des laufenden Spiels anwenden. DeckGlass wandelt RetroArch-Presets (`.slangp`) direkt auf dem Deck in einen solchen Effekt um:

```
.slangp/.slang ─► glslang ─► SPIR-V ─► SPIRV-Cross (HLSL) ─► slangfx ─► eine ReShade-.fx
```

- Mehrere Durchläufe, Aliase, Feedback, `OriginalHistory`, LUT-Texturen und Parameter werden unterstützt.
- **Quellauflösung:** Der erste Durchlauf rechnet das Spielbild auf die Originalauflösung des Systems herunter (z. B. PS2 640×448), damit Scanlines und Masken stimmen. Angenommen wird ein bildschirmfüllend skaliertes Bild (ohne Integer-Skalierung). Alternativ manuell über eine Pixelgröße.
- Parameter lassen sich im Plugin einstellen; jede Änderung erzeugt eine neue `.fx`, die gamescope sofort lädt.

## Shader

Die Auswahl funktioniert wie ein Dateiexplorer (wie in RetroArch) mit zwei Überordnern:

- **RetroArch:** die [libretro slang-shaders](https://github.com/libretro/slang-shaders). Ausgeblendet sind Mega Bezel, HDR-Shader, Testbilder und reine Quelltext-Ordner.
- **RetroCrisis:** die [Retro Crisis GDV-NTSC](https://github.com/RetroCrisis/Retro-Crisis-GDV-NTSC)-Presets (GPL-3), inklusive des Ordners „720p Steam Deck“.

## Bedienung

- **Shader aktiv / Nur im Spiel:** „Nur im Spiel“ lässt die Steam-Menüs unberührt. War es aus, startet der Shader nach einem Neustart ausgeschaltet.
- **Pro Spiel:** Während ein Spiel läuft, speichert dieser Schalter alle Einstellungen nur für dieses Spiel. Beim Beenden gelten wieder die globalen Einstellungen.
- **Parameter:** eigenes Fenster mit allen Reglern der Voreinstellung.

## Einschränkungen

- Screenshots enthalten den Shader nicht.
- Schwere Presets (z. B. crt-royale) kosten viel GPU-Leistung.
- Ein kleiner Teil der Presets lässt sich nicht umwandeln (z. B. Matrix-Ternaries, HDR-Spezialfälle); der Fehler wird im Plugin angezeigt.

## Bauen

```bash
pnpm install
./package.sh /pfad/zu/slang-shaders [/pfad/zum/entpackten/retro-crisis-release]
```

Ergebnis: `out/DeckGlass-<version>.zip`, installierbar über Decky (Entwicklermodus → „Install Plugin from ZIP“).

## Lizenz

GPL-3.0 (siehe `LICENSE`), wie Reshadeck. Mitgelieferte Werkzeuge: glslang und SPIRV-Cross (Lizenzen in `slangfx/bin/licenses/`). Die Shader stehen unter den Lizenzen ihrer jeweiligen Autoren.
