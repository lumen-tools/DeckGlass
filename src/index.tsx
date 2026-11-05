import {
  ButtonItem,
  DialogButton,
  Dropdown,
  DropdownOption,
  Focusable,
  ModalRoot,
  PanelSection,
  PanelSectionRow,
  Router,
  SliderField,
  ToggleField,
  showModal,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { useEffect, useMemo, useRef, useState } from "react";
import { MdMonitor } from "react-icons/md";

// ------------------------------------------------------------------ types & backend

type Preset = { key: string; label: string };
type Folder = { name: string; folders: Folder[]; presets: Preset[] };
type System = { id: string; label: string; w: number; h: number };
type Param = { name: string; label: string; min: number; max: number; step: number; default: number; value: number };
type Settings = {
  enabled: boolean;
  preset: string | null;
  scale_mode: "system" | "manual";
  system: string;
  pixel_size: number;
  only_in_game: boolean;
  is_oled: boolean;
  oled_defringe: boolean;
  last_error: string | null;
  app_id: string | null;
  app_name: string | null;
  profile_active: boolean;
};
type ApplyResult = { ok: boolean; error?: string; warning?: string | null; params: Param[] };

const getLibrary = callable<[], Folder>("get_library");
const getSystems = callable<[], System[]>("get_systems");
const getSettings = callable<[], Settings>("get_settings");
const getParams = callable<[], Param[]>("get_params");
const updateSettings = callable<[Partial<Settings>], ApplyResult>("update_settings");
const setParam = callable<[string, string, number], ApplyResult>("set_param");
const resetParams = callable<[string], ApplyResult>("reset_params");
const reapply = callable<[], ApplyResult>("reapply");
const setInGame = callable<[boolean, string | null, string | null], ApplyResult>("set_in_game");
const setProfile = callable<[boolean], ApplyResult>("set_profile");

// The side panel listens here so dialogs can tell it to refresh.
const listeners = new Set<() => void>();
const notifyChanged = () => listeners.forEach((l) => l());

// Deck screen; the pixel size shown per system assumes the game fills it.
const SCREEN_W = 1280;
const SCREEN_H = 800;
const pixelSizeOf = (s: System) => Math.min(SCREEN_W / s.w, SCREEN_H / s.h);

// ------------------------------------------------------------------ helpers

/** Folder names from the library root to the folder that holds `key`. */
function pathOf(root: Folder, key: string | null): string[] {
  if (!key) return [];
  const walk = (f: Folder, trail: string[]): string[] | null => {
    if (f.presets.some((p) => p.key === key)) return trail;
    for (const sub of f.folders) {
      const r = walk(sub, [...trail, sub.name]);
      if (r) return r;
    }
    return null;
  };
  return walk(root, []) ?? [];
}

function folderAt(root: Folder, path: string[]): Folder {
  let cur = root;
  for (const name of path) {
    const next = cur.folders.find((f) => f.name === name);
    if (!next) break;
    cur = next;
  }
  return cur;
}

function presetLabel(root: Folder | null, key: string | null): string {
  if (!root || !key) return "None selected";
  const p = folderAt(root, pathOf(root, key)).presets.find((x) => x.key === key);
  return p ? p.label : key.split("/").pop()!.replace(/\.slangp$/i, "");
}

const rowStyle = {
  display: "flex",
  justifyContent: "flex-start",
  textAlign: "left" as const,
  width: "100%",
  marginBottom: "4px",
  padding: "8px 12px",
  minWidth: 0,
};

const titleStyle = { fontWeight: "bold" as const, fontSize: "18px", marginBottom: "4px" };
const subtitleStyle = { fontSize: "13px", opacity: 0.7, marginBottom: "10px" };

// ------------------------------------------------------------------ shader browser

function Browser({ root, current, closeModal }: { root: Folder; current: string | null; closeModal?: () => void }) {
  const [path, setPath] = useState<string[]>(() => pathOf(root, current));
  const folder = folderAt(root, path);

  const choose = async (key: string) => {
    closeModal?.();
    await updateSettings({ preset: key, enabled: true });
    notifyChanged();
  };

  return (
    <ModalRoot onCancel={closeModal} onEscKeypress={closeModal}>
      <div style={titleStyle}>Choose Preset</div>
      <div style={subtitleStyle}>{path.length ? path.join(" › ") : "All shaders"}</div>
      <Focusable style={{ maxHeight: "60vh", overflowY: "auto", display: "flex", flexDirection: "column" }}>
        {path.length > 0 && (
          <DialogButton style={rowStyle} onClick={() => setPath(path.slice(0, -1))}>
            ‹  Back
          </DialogButton>
        )}
        {folder.folders.map((f) => (
          <DialogButton key={"d" + f.name} style={rowStyle} onClick={() => setPath([...path, f.name])}>
            📁  {f.name}
          </DialogButton>
        ))}
        {folder.presets.map((p) => (
          <DialogButton key={p.key} style={rowStyle} onClick={() => choose(p.key)}>
            {p.key === current ? "✓  " : ""}
            {p.label}
          </DialogButton>
        ))}
        {folder.folders.length === 0 && folder.presets.length === 0 && (
          <div style={{ opacity: 0.7, padding: "8px" }}>This folder is empty.</div>
        )}
      </Focusable>
    </ModalRoot>
  );
}

// ------------------------------------------------------------------ parameter dialog

function ParamDialog({ preset, title, closeModal }: { preset: string; title: string; closeModal?: () => void }) {
  const [params, setParams] = useState<Param[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  useEffect(() => {
    getParams().then(setParams);
  }, []);

  const handle = (r: ApplyResult) => {
    setError(r.ok ? r.warning ?? null : r.error ?? "Unknown error");
    notifyChanged();
  };

  const onChange = (p: Param, v: number) => {
    setParams((ps) => (ps ?? []).map((x) => (x.name === p.name ? { ...x, value: v } : x)));
    clearTimeout(timers.current[p.name]);
    timers.current[p.name] = setTimeout(() => setParam(preset, p.name, v).then(handle), 500);
  };

  return (
    <ModalRoot onCancel={closeModal} onEscKeypress={closeModal}>
      <div style={titleStyle}>Parameters</div>
      <div style={subtitleStyle}>{title}</div>
      {error && <div style={{ color: "#ff7070", fontSize: "13px", marginBottom: "8px" }}>{error}</div>}
      <Focusable style={{ maxHeight: "60vh", overflowY: "auto" }}>
        {params === null && <div>Loading …</div>}
        {params !== null && params.length === 0 && <div>This preset has no parameters.</div>}
        {params !== null && params.length > 0 && (
          <DialogButton
            style={{ marginBottom: "8px" }}
            onClick={async () => {
              handle(await resetParams(preset));
              setParams(await getParams());
            }}
          >
            Reset all to defaults
          </DialogButton>
        )}
        {(params ?? []).map((p) =>
          p.min === p.max ? (
            // entries such as "[ AFTERGLOW SETTINGS ]" have no range: show them as headings
            <div key={p.name} style={{ marginTop: "12px", fontWeight: "bold", opacity: 0.8 }}>
              {p.label.replace(/^[\s[\]\-=:*#]+|[\s[\]\-=:*#]+$/g, "")}
            </div>
          ) : (
            <SliderField
              key={p.name}
              label={p.label}
              min={p.min}
              max={p.max}
              step={p.step > 0 ? p.step : (p.max - p.min) / 100}
              value={p.value}
              showValue
              editableValue
              onChange={(v) => onChange(p, v)}
            />
          )
        )}
      </Focusable>
    </ModalRoot>
  );
}

// ------------------------------------------------------------------ side panel

function Content() {
  const [library, setLibrary] = useState<Folder | null>(null);
  const [systems, setSystems] = useState<System[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pxTimer = useRef<ReturnType<typeof setTimeout>>();

  const run = async (fn: () => Promise<ApplyResult>) => {
    setBusy(true);
    try {
      const r = await fn();
      setError(r.ok ? r.warning ?? null : r.error ?? "Unknown error");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const refresh = async () => {
    const st = await getSettings();
    setSettings(st);
    setError(st.last_error);
  };

  useEffect(() => {
    (async () => {
      const [l, s] = await Promise.all([getLibrary(), getSystems()]);
      setLibrary(l);
      setSystems(s);
      await refresh();
      await run(() => reapply());
    })();
    const l = () => {
      refresh();
    };
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);

  const change = async (c: Partial<Settings>) => {
    setSettings((s) => (s ? { ...s, ...c } : s));
    await run(() => updateSettings(c));
    await refresh();
  };

  const modeOptions: DropdownOption[] = [
    { data: "system", label: "Presets" },
    { data: "manual", label: "Manual" },
  ];
  const systemOptions: DropdownOption[] = useMemo(
    () =>
      systems.map((s) => ({
        data: s.id,
        label: `${s.label} (${s.w}×${s.h}) – ${pixelSizeOf(s).toFixed(2)}×`,
      })),
    [systems]
  );

  if (!settings || !library) {
    return (
      <PanelSection>
        <PanelSectionRow>Loading …</PanelSectionRow>
      </PanelSection>
    );
  }

  const label = presetLabel(library, settings.preset);
  const where = settings.preset ? pathOf(library, settings.preset).join(" › ") : "";

  return (
    <>
      <PanelSection>
        <PanelSectionRow>
          <ToggleField
            label="Enable shader"
            checked={settings.enabled}
            disabled={busy || !settings.preset}
            onChange={(v) => change({ enabled: v })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Only in games"
            description="Keep the Steam menus unshaded"
            checked={settings.only_in_game}
            disabled={busy}
            onChange={(v) => change({ only_in_game: v })}
          />
        </PanelSectionRow>
        {settings.is_oled && (
          <PanelSectionRow>
            <ToggleField
              label="OLED defringe"
              description="Reduces colour fringing on the Deck OLED screen"
              checked={settings.oled_defringe}
              disabled={busy}
              onChange={(v) => change({ oled_defringe: v })}
            />
          </PanelSectionRow>
        )}
        {busy && (
          <PanelSectionRow>
            <div style={{ fontSize: "12px", opacity: 0.8 }}>Converting shader …</div>
          </PanelSectionRow>
        )}
        {error && (
          <PanelSectionRow>
            <div style={{ fontSize: "12px", color: "#ff7070", wordBreak: "break-word" }}>{error}</div>
          </PanelSectionRow>
        )}
      </PanelSection>

      {settings.app_id && (
        <PanelSection title="Game Profile">
          <PanelSectionRow>
            <ToggleField
              label="Per-game settings"
              description={
                settings.profile_active
                  ? `Only applies to ${settings.app_name ?? "this game"}`
                  : `Turn on to give ${settings.app_name ?? "this game"} its own settings`
              }
              checked={settings.profile_active}
              disabled={busy}
              onChange={async (v) => {
                await run(() => setProfile(v));
                await refresh();
              }}
            />
          </PanelSectionRow>
        </PanelSection>
      )}

      <PanelSection title="Shader">
        <PanelSectionRow>
          <div style={{ fontSize: "14px" }}>
            <div style={{ fontWeight: "bold", wordBreak: "break-word" }}>{label}</div>
            {where && <div style={{ fontSize: "12px", opacity: 0.7, wordBreak: "break-word" }}>{where}</div>}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy}
            onClick={() => showModal(<Browser root={library} current={settings.preset} />)}
          >
            Choose Preset …
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy || !settings.preset}
            onClick={() => settings.preset && showModal(<ParamDialog preset={settings.preset} title={label} />)}
          >
            Parameters …
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Source Resolution">
        <PanelSectionRow>
          <Dropdown
            menuLabel="Source Resolution"
            rgOptions={modeOptions}
            selectedOption={settings.scale_mode}
            onChange={(o) => change({ scale_mode: o.data })}
          />
        </PanelSectionRow>
        {settings.scale_mode === "system" && (
          <PanelSectionRow>
            <Dropdown
              menuLabel="System (pixel size on the Deck screen)"
              rgOptions={systemOptions}
              selectedOption={settings.system}
              onChange={(o) => change({ system: o.data })}
            />
          </PanelSectionRow>
        )}
        {settings.scale_mode === "manual" && (
          <PanelSectionRow>
            <SliderField
              label="Pixel size"
              description="Screen pixels per original pixel"
              min={1}
              max={8}
              step={0.05}
              value={settings.pixel_size}
              showValue
              editableValue
              onChange={(v) => {
                setSettings((s) => (s ? { ...s, pixel_size: v } : s));
                clearTimeout(pxTimer.current);
                pxTimer.current = setTimeout(() => change({ pixel_size: v }), 500);
              }}
            />
          </PanelSectionRow>
        )}
      </PanelSection>
    </>
  );
}

export default definePlugin(() => {
  // Tell the backend which game is running (per-game profiles, Steam UI stays unshaded).
  const report = () => {
    const app = Router.MainRunningApp as any;
    return setInGame(!!app, app ? String(app.appid) : null, app?.display_name ?? null)
      .then(() => notifyChanged())
      .catch(() => {});
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
