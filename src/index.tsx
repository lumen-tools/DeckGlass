import {
  ButtonItem,
  Dropdown,
  DropdownOption,
  PanelSection,
  PanelSectionRow,
  SliderField,
  Router,
  ToggleField,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { useEffect, useMemo, useRef, useState } from "react";
import { MdMonitor } from "react-icons/md";

type Preset = { key: string; label: string };
type Group = { folder: string; presets: Preset[] };
type Library = { library: string; groups: Group[] };
type System = { id: string; label: string; w: number; h: number };
type Param = { name: string; label: string; min: number; max: number; step: number; default: number; value: number };
type Settings = {
  enabled: boolean;
  preset: string | null;
  scale_mode: "system" | "manual";
  system: string;
  pixel_size: number;
  only_in_game: boolean;
  last_error: string | null;
  app_id: string | null;
  app_name: string | null;
  profile_active: boolean;
};
type ApplyResult = { ok: boolean; error?: string; warning?: string | null; params: Param[] };

const getLibrary = callable<[], Library[]>("get_library");
const getSystems = callable<[], System[]>("get_systems");
const getSettings = callable<[], Settings>("get_settings");
const updateSettings = callable<[Partial<Settings>], ApplyResult>("update_settings");
const setParam = callable<[string, string, number], ApplyResult>("set_param");
const resetParams = callable<[string], ApplyResult>("reset_params");
const reapply = callable<[], ApplyResult>("reapply");
const setInGame = callable<[boolean, string | null, string | null], ApplyResult>("set_in_game");
const setProfile = callable<[boolean], ApplyResult>("set_profile");

// Browsing state lives outside the component: Decky may remount the panel (for
// example when a dropdown menu closes), which would otherwise reset the selection.
const browse: { lib: string | null; folder: string | null } = { lib: null, folder: null };


const MANUAL = "__manual__";

function splitKey(key: string | null): { library: string; folder: string } | null {
  if (!key) return null;
  const i = key.indexOf("/");
  const rel = key.slice(i + 1);
  const j = rel.lastIndexOf("/");
  return { library: key.slice(0, i), folder: j < 0 ? "." : rel.slice(0, j) };
}

function preferredFolder(l: Library | undefined): string {
  if (!l) return "";
  return (l.groups.find((g) => /steam deck/i.test(g.folder)) ?? l.groups[0])?.folder ?? "";
}

function Content() {
  const [library, setLibrary] = useState<Library[]>([]);
  const [systems, setSystems] = useState<System[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [lib, setLibState] = useState<string>(browse.lib ?? "");
  const [folder, setFolderState] = useState<string>(browse.folder ?? "");
  const setLib = (v: string) => {
    browse.lib = v;
    setLibState(v);
  };
  const setFolder = (v: string) => {
    browse.folder = v;
    setFolderState(v);
  };
  const [params, setParams] = useState<Param[]>([]);
  const [showParams, setShowParams] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const handle = (r: ApplyResult) => {
    setError(r.ok ? r.warning ?? null : r.error ?? "Unbekannter Fehler");
    setParams(r.params ?? []);
  };

  const run = async (fn: () => Promise<ApplyResult>) => {
    setBusy(true);
    try {
      handle(await fn());
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const loadSettings = async (l: Library[]) => {
    const st = await getSettings();
    setSettings(st);
    if (browse.lib === null || !l.some((x) => x.library === browse.lib)) {
      const sk = splitKey(st.preset);
      const firstLib = sk?.library ?? l[0]?.library ?? "";
      setLib(firstLib);
      setFolder(sk?.folder ?? preferredFolder(l.find((x) => x.library === firstLib)));
    }
    setError(st.last_error);
    if (st.preset) await run(() => reapply());
  };

  useEffect(() => {
    (async () => {
      const [l, s] = await Promise.all([getLibrary(), getSystems()]);
      setLibrary(l);
      setSystems(s);
      await loadSettings(l);
    })();
  }, []);

  const change = async (c: Partial<Settings>) => {
    setSettings((s) => (s ? { ...s, ...c } : s));
    await run(() => updateSettings(c));
  };

  const libObj = library.find((x) => x.library === lib);
  const groupObj = libObj?.groups.find((g) => g.folder === folder);

  const libOptions: DropdownOption[] = library.map((l) => ({ data: l.library, label: l.library }));
  const folderOptions: DropdownOption[] = (libObj?.groups ?? []).map((g) => ({
    data: g.folder,
    label: `${g.folder === "." ? "(Hauptordner)" : g.folder} (${g.presets.length})`,
  }));
  const presetOptions: DropdownOption[] = (groupObj?.presets ?? []).map((p) => ({ data: p.key, label: p.label }));
  const scaleOptions: DropdownOption[] = useMemo(
    () => [
      ...systems.map((s) => ({ data: s.id, label: `${s.label} (${s.w}×${s.h})` })),
      { data: MANUAL, label: "Manuell: Pixelgröße" },
    ],
    [systems]
  );

  if (!settings) {
    return (
      <PanelSection>
        <PanelSectionRow>Lade …</PanelSectionRow>
      </PanelSection>
    );
  }

  const currentLabel = settings.preset ? settings.preset.split("/").pop()?.replace(/\.slangp$/, "") : "—";

  const onParam = (p: Param, v: number) => {
    setParams((ps) => ps.map((x) => (x.name === p.name ? { ...x, value: v } : x)));
    const preset = settings.preset;
    if (!preset) return;
    clearTimeout(timers.current[p.name]);
    timers.current[p.name] = setTimeout(() => run(() => setParam(preset, p.name, v)), 500);
  };

  return (
    <>
      <PanelSection>
        <PanelSectionRow>
          <ToggleField
            label="Shader aktiv"
            checked={settings.enabled}
            disabled={busy}
            onChange={(v) => change({ enabled: v })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Nur im Spiel"
            description="Steam-Menüs bleiben ohne Shader"
            checked={settings.only_in_game}
            disabled={busy}
            onChange={(v) => change({ only_in_game: v })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ fontSize: "12px" }}>
            Aktuell: <b>{currentLabel}</b>
            {busy ? " · wird umgewandelt …" : ""}
          </div>
        </PanelSectionRow>
        {error && (
          <PanelSectionRow>
            <div style={{ fontSize: "12px", color: "#ff7070", wordBreak: "break-word" }}>{error}</div>
          </PanelSectionRow>
        )}
      </PanelSection>

      {settings.app_id && (
        <PanelSection title="Spielprofil">
          <PanelSectionRow>
            <ToggleField
              label="Pro Spiel"
              description={
                settings.profile_active
                  ? `Einstellungen gelten nur für ${settings.app_name ?? "dieses Spiel"}`
                  : `Globale Einstellungen – einschalten, um ${settings.app_name ?? "dieses Spiel"} eigene zu geben`
              }
              checked={settings.profile_active}
              disabled={busy}
              onChange={async (v) => {
                await run(() => setProfile(v));
                await loadSettings(library);
              }}
            />
          </PanelSectionRow>
        </PanelSection>
      )}

      <PanelSection title="Shader">
        <PanelSectionRow>
          <Dropdown
            menuLabel="Sammlung"
            rgOptions={libOptions}
            selectedOption={lib}
            onChange={(o) => {
              setLib(o.data);
              const l = library.find((x) => x.library === o.data);
              setFolder(preferredFolder(l));
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <Dropdown menuLabel="Ordner" rgOptions={folderOptions} selectedOption={folder} onChange={(o) => setFolder(o.data)} />
        </PanelSectionRow>
        <PanelSectionRow>
          <Dropdown
            menuLabel="Preset"
            strDefaultLabel="Preset wählen"
            rgOptions={presetOptions}
            selectedOption={settings.preset}
            onChange={(o) => change({ preset: o.data, enabled: true })}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Quellauflösung">
        <PanelSectionRow>
          <Dropdown
            menuLabel="System"
            rgOptions={scaleOptions}
            selectedOption={settings.scale_mode === "manual" ? MANUAL : settings.system}
            onChange={(o) =>
              o.data === MANUAL ? change({ scale_mode: "manual" }) : change({ scale_mode: "system", system: o.data })
            }
          />
        </PanelSectionRow>
        {settings.scale_mode === "manual" && (
          <PanelSectionRow>
            <SliderField
              label="Pixelgröße"
              description="Bildschirmpixel pro Original-Pixel"
              min={1}
              max={8}
              step={0.05}
              value={settings.pixel_size}
              showValue
              editableValue
              onChange={(v) => {
                setSettings((s) => (s ? { ...s, pixel_size: v } : s));
                clearTimeout(timers.current.__px);
                timers.current.__px = setTimeout(() => change({ pixel_size: v }), 500);
              }}
            />
          </PanelSectionRow>
        )}
      </PanelSection>

      {params.length > 0 && (
        <PanelSection title={`Parameter (${params.length})`}>
          <PanelSectionRow>
            <ToggleField label="Parameter anzeigen" checked={showParams} onChange={setShowParams} />
          </PanelSectionRow>
          {showParams && (
            <>
              <PanelSectionRow>
                <ButtonItem layout="below" disabled={busy} onClick={() => settings.preset && run(() => resetParams(settings.preset!))}>
                  Auf Standard zurücksetzen
                </ButtonItem>
              </PanelSectionRow>
              {params.map((p) =>
                p.min === p.max ? null : (
                  <PanelSectionRow key={p.name}>
                    <SliderField
                      label={p.label}
                      min={p.min}
                      max={p.max}
                      step={p.step > 0 ? p.step : (p.max - p.min) / 100}
                      value={p.value}
                      showValue
                      editableValue
                      onChange={(v) => onParam(p, v)}
                    />
                  </PanelSectionRow>
                )
              )}
            </>
          )}
        </PanelSection>
      )}
    </>
  );
}

export default definePlugin(() => {
  // Tell the backend whether a game is running, so the Steam UI can stay unshaded.
  const report = () => {
    const app = Router.MainRunningApp as any;
    return setInGame(!!app, app ? String(app.appid) : null, app?.display_name ?? null).catch(() => {});
  };
  report();
  const reg = (window as any).SteamClient?.GameSessions?.RegisterForAppLifetimeNotifications?.(() => {
    setTimeout(report, 500);
  });
  return {
    name: "DeckGlass",
    titleView: <div className={staticClasses.Title}>DeckGlass</div>,
    content: <Content />,
    icon: <MdMonitor />,
    onDismount() {
      reg?.unregister?.();
    },
  };
});
