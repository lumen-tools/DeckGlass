import {
  ButtonItem,
  ConfirmModal,
  DialogButton,
  Dropdown,
  DropdownOption,
  Focusable,
  ModalRoot,
  PanelSection,
  PanelSectionRow,
  Router,
  SliderField,
  TextField,
  ToggleField,
  showModal,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin, toaster } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import { MdMonitor } from "react-icons/md";

// ------------------------------------------------------------------ types & backend

type Preset = { key: string; label: string };
type Folder = { name: string; folders: Folder[]; presets: Preset[] };
type System = { id: string; label: string; w: number; h: number };
type ParamOption = { value: number; label: string };
type Param = {
  name: string;
  label: string;
  min: number;
  max: number;
  step: number;
  default: number;
  value: number;
  kind: "heading" | "choice" | "slider";
  options?: ParamOption[];
};

/** "-1 = Auto · 0 = NO · 1 = YES" */
const optionLegend = (opts: ParamOption[]) => opts.map((o) => `${o.value} = ${o.label}`).join(" · ");
const optionName = (p: Param) => p.options?.find((o) => Math.abs(o.value - p.value) < 1e-6)?.label;
type Settings = {
  enabled: boolean;
  preset: string | null;
  scale_mode: "system" | "manual";
  system: string;
  pixel_size: number;
  game_height: number;
  bilinear: boolean;
  only_in_game: boolean;
  state: string | null;
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
const resetParams = callable<[string], ApplyResult>("reset_params");
const reapply = callable<[], ApplyResult>("reapply");
const setInGame = callable<[boolean, string | null, string | null], ApplyResult>("set_in_game");
const setProfile = callable<[boolean], ApplyResult>("set_profile");
const getCustomDir = callable<[], string>("get_custom_dir");
const setParams = callable<[string, Record<string, number>], ApplyResult>("set_params");

/** Whether a shader is on screen (gamescope recompiles it on every change). */
const effectLive = (s: Settings | null) =>
  !!s && s.enabled && !!s.preset && (s.app_id !== null || !s.only_in_game);

/** Warn before a change that makes gamescope compile the shader: the whole picture,
 *  this menu included, pauses until it is done. */
async function withNotice<T>(live: boolean, fn: () => Promise<T>): Promise<T> {
  if (live) {
    toaster.toast({
      title: "DeckGlass",
      body: "Applying the shader – the picture may pause for a moment while gamescope compiles it.",
      duration: 5000,
    });
    await new Promise((r) => setTimeout(r, 400)); // let the notice appear first
  }
  return fn();
}
type SaveState = { name: string; preset: string | null };
const getStates = callable<[], SaveState[]>("get_states");
const saveState = callable<[string], ApplyResult>("save_state");
const loadState = callable<[string], ApplyResult>("load_state");
const deleteState = callable<[string], ApplyResult>("delete_state");

// The side panel listens here so dialogs can tell it to refresh.
const listeners = new Set<() => void>();
const notifyChanged = () => listeners.forEach((l) => l());



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

function Browser({
  root,
  current,
  live,
  closeModal,
}: {
  root: Folder;
  current: string | null;
  live: boolean;
  closeModal?: () => void;
}) {
  const [path, setPath] = useState<string[]>(() => pathOf(root, current));
  const [customDir, setCustomDir] = useState<string>("~/DeckGlass/Custom_Shaders");
  useEffect(() => {
    getCustomDir().then(setCustomDir).catch(() => {});
  }, []);
  const folder = folderAt(root, path);

  const choose = async (key: string) => {
    closeModal?.();
    await withNotice(live, () => updateSettings({ preset: key, enabled: true }));
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
          <div style={{ opacity: 0.7, padding: "8px", wordBreak: "break-word" }}>
            {path.length === 1 && path[0] === "Custom_Shaders"
              ? `No presets yet. Copy slang presets (.slangp) with their folders to ${customDir} – for example an unpacked Retro Crisis release. Then reopen this dialog.`
              : "This folder is empty."}
          </div>
        )}
      </Focusable>
    </ModalRoot>
  );
}

// ------------------------------------------------------------------ system picker

/** Pixel size with at most one decimal (same rule as the converter). */
const nicePixelSize = (exact: number) => Math.max(1, Math.floor(exact * 10 + 0.5) / 10);
const factorFor = (s: System, lines: number) =>
  nicePixelSize(s.w ? Math.min(1280 / s.w, lines / s.h) : lines / s.h);
const fmt = (f: number) => `${Math.round(f * 100) / 100}×`;
const systemLabel = (s: System) => {
  const res = s.w ? ` (${s.w}×${s.h})` : "";
  const a = factorFor(s, 720);
  const b = factorFor(s, 800);
  const px = Math.abs(a - b) < 0.005 ? fmt(a) : `720p ${fmt(a)} · 800p ${fmt(b)}`;
  return `${s.label}${res} · ${px}`;
};

function SystemPicker({
  systems,
  current,
  live,
  closeModal,
}: {
  systems: System[];
  current: string;
  live: boolean;
  closeModal?: () => void;
}) {
  const choose = async (id: string) => {
    closeModal?.();
    await withNotice(live, () => updateSettings({ scale_mode: "system", system: id }));
    notifyChanged();
  };
  return (
    <ModalRoot onCancel={closeModal} onEscKeypress={closeModal}>
      <div style={titleStyle}>Choose System</div>
      <div style={subtitleStyle}>The pixel size follows from the height of the game picture (720 or 800 lines)</div>
      <Focusable style={{ maxHeight: "60vh", overflowY: "auto", display: "flex", flexDirection: "column" }}>
        {systems.map((s) => (
          <DialogButton key={s.id} style={rowStyle} onClick={() => choose(s.id)}>
            {s.id === current ? "✓  " : ""}
            {systemLabel(s)}
          </DialogButton>
        ))}
      </Focusable>
    </ModalRoot>
  );
}

// ------------------------------------------------------------------ save states

const slug = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9.]+/g, "-")
    .replace(/^-+|-+$/g, "");

/** e.g. "retro-crisis-ps2-dirty-1.5x" */
function suggestName(settings: Settings, label: string, systems: System[]): string {
  const lib = settings.preset ? settings.preset.split("/")[0] : "";
  let px: number | null = null;
  if (settings.scale_mode === "manual") px = settings.pixel_size;
  else {
    const s = systems.find((x) => x.id === settings.system);
    if (s) px = factorFor(s, settings.game_height === 800 ? 800 : 720);
  }
  const parts = [slug(lib), slug(label)];
  if (px) parts.push(`${Math.round(px * 100) / 100}x`);
  return parts.filter(Boolean).join("-");
}

function SaveDialog({
  initial,
  existing,
  closeModal,
}: {
  initial: string;
  existing: string[];
  closeModal?: () => void;
}) {
  const [name, setName] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const trimmed = name.trim();
  const replaces = existing.some((n) => n === trimmed);
  const save = async () => {
    const r = await saveState(trimmed);
    if (!r.ok) {
      setError(r.error ?? "Could not save");
      return;
    }
    closeModal?.();
    notifyChanged();
  };
  return (
    <ModalRoot onCancel={closeModal} onEscKeypress={closeModal}>
      <div style={titleStyle}>Save State</div>
      <div style={subtitleStyle}>Stores the shader, source resolution and all parameters under a name.</div>
      <TextField label="Name" value={name} onChange={(e) => setName(e.target.value)} />
      {replaces && (
        <div style={{ fontSize: "13px", opacity: 0.8, marginTop: "6px" }}>
          A save state with this name exists and will be replaced.
        </div>
      )}
      {error && <div style={{ color: "#ff7070", fontSize: "13px", marginTop: "6px" }}>{error}</div>}
      <Focusable style={{ display: "flex", gap: "8px", marginTop: "12px" }}>
        <DialogButton disabled={!trimmed} onClick={save}>
          {replaces ? "Replace" : "Save"}
        </DialogButton>
        <DialogButton onClick={() => closeModal?.()}>Cancel</DialogButton>
      </Focusable>
    </ModalRoot>
  );
}

// ------------------------------------------------------------------ parameter dialog

function ParamDialog({
  preset,
  title,
  live,
  closeModal,
}: {
  preset: string;
  title: string;
  live: boolean;
  closeModal?: () => void;
}) {
  const [params, setParamsState] = useState<Param[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // changes are collected and applied together: every apply makes gamescope recompile
  const [pending, setPending] = useState<Record<string, number>>({});
  const dirty = Object.keys(pending).length > 0;

  useEffect(() => {
    getParams().then(setParamsState);
  }, []);

  const handle = (r: ApplyResult) => {
    setError(r.ok ? r.warning ?? null : r.error ?? "Unknown error");
    notifyChanged();
  };

  const onChange = (p: Param, v: number) => {
    setParamsState((ps) => (ps ?? []).map((x) => (x.name === p.name ? { ...x, value: v } : x)));
    setPending((pd) => ({ ...pd, [p.name]: v }));
  };

  const apply = async () => {
    if (!dirty) return;
    const values = pending;
    setPending({});
    handle(await withNotice(live, () => setParams(preset, values)));
  };

  const close = async () => {
    closeModal?.();
    await apply();
  };

  return (
    <ModalRoot onCancel={close} onEscKeypress={close}>
      <div style={titleStyle}>Parameters</div>
      <div style={subtitleStyle}>{title}</div>
      <div style={{ fontSize: "12px", opacity: 0.7, marginBottom: "8px" }}>
        Changes are applied when you press Apply or close this dialog.
      </div>
      {error && <div style={{ color: "#ff7070", fontSize: "13px", marginBottom: "8px" }}>{error}</div>}
      <Focusable style={{ maxHeight: "60vh", overflowY: "auto" }}>
        {params === null && <div>Loading …</div>}
        {params !== null && params.length === 0 && <div>This preset has no parameters.</div>}
        {params !== null && params.length > 0 && (
          <Focusable style={{ display: "flex", gap: "8px", marginBottom: "8px" }}>
            <DialogButton disabled={!dirty} onClick={apply}>
              Apply
            </DialogButton>
            <DialogButton
              onClick={async () => {
                setPending({});
                handle(await withNotice(live, () => resetParams(preset)));
                setParamsState(await getParams());
              }}
            >
              Reset all to defaults
            </DialogButton>
          </Focusable>
        )}
        {(params ?? []).map((p) =>
          p.kind === "heading" ? (
            <div key={p.name} style={{ marginTop: "12px", fontWeight: "bold", opacity: 0.8 }}>
              {p.label}
            </div>
          ) : (
            <SliderField
              key={p.name}
              label={p.options && optionName(p) ? `${p.label}: ${optionName(p)}` : p.label}
              description={p.options ? optionLegend(p.options) : undefined}
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
  const [states, setStates] = useState<SaveState[]>([]);
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
    const [st, sv] = await Promise.all([getSettings(), getStates()]);
    setSettings(st);
    setStates(sv);
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
    const next = settings ? { ...settings, ...c } : null;
    setSettings(next);
    // only "Only in games" and OLED defringe change nothing on a running shader's code path
    const recompiles = effectLive(next) && !("only_in_game" in c && Object.keys(c).length === 1);
    await run(() => withNotice(recompiles, () => updateSettings(c)));
    await refresh();
  };

  const modeOptions: DropdownOption[] = [
    { data: "system", label: "Presets" },
    { data: "manual", label: "Manual" },
  ];


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
            disabled={!settings.preset}
            onChange={(v) => change({ enabled: v })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Only in games"
            description="Keep the Steam menus unshaded"
            checked={settings.only_in_game}
            onChange={(v) => change({ only_in_game: v })}
          />
        </PanelSectionRow>
        {settings.is_oled && (
          <PanelSectionRow>
            <ToggleField
              label="OLED defringe"
              description="Reduces colour fringing on the Deck OLED screen"
              checked={settings.oled_defringe}
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
              onChange={async (v) => {
                await run(() => withNotice(effectLive(settings), () => setProfile(v)));
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
            onClick={async () => {
              // re-scan so presets copied into the Custom folder show up right away
              const fresh = await getLibrary().catch(() => library);
              setLibrary(fresh);
              // choosing a preset turns the shader on, so it is on screen in a game or globally
              showModal(
                <Browser root={fresh} current={settings.preset} live={!!settings.app_id || !settings.only_in_game} />
              );
            }}
          >
            Choose Preset …
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={!settings.preset}
            onClick={() =>
              settings.preset &&
              showModal(<ParamDialog preset={settings.preset} title={label} live={effectLive(settings)} />)
            }
          >
            Parameters …
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Save States">
        <PanelSectionRow>
          <Dropdown
            menuLabel="Save state"
            strDefaultLabel={states.length ? "Not saved" : "No save states yet"}
            rgOptions={states.map((s) => ({ data: s.name, label: s.name }))}
            selectedOption={settings.state}
            disabled={!states.length}
            onChange={async (o) => {
              await run(() => withNotice(effectLive(settings) || !!settings.app_id || !settings.only_in_game, () => loadState(o.data)));
              await refresh();
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={!settings.preset}
            onClick={() =>
              showModal(
                <SaveDialog
                  initial={settings.state ?? suggestName(settings, label, systems)}
                  existing={states.map((s) => s.name)}
                />
              )
            }
          >
            Save as …
          </ButtonItem>
        </PanelSectionRow>
        {settings.state && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={() => {
                const name = settings.state!;
                showModal(
                  <ConfirmModal
                    strTitle="Delete save state"
                    strDescription={`Delete "${name}"? The current settings stay as they are.`}
                    strOKButtonText="Delete"
                    onOK={async () => {
                      await run(() => deleteState(name));
                      await refresh();
                    }}
                  />
                );
              }}
            >
              Delete "{settings.state}"
            </ButtonItem>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <div style={{ fontSize: "12px", opacity: 0.7 }}>
            Parameters reset when you choose another shader or save state. Save a state to keep them.
          </div>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Source Resolution">
        <PanelSectionRow>
          <ToggleField
            label="Bilinear filtering"
            description="Smooths the game image before the shader – usually looks closer to RetroArch"
            checked={settings.bilinear}
            onChange={(v) => change({ bilinear: v })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <Dropdown
            menuLabel="Source Resolution"
            rgOptions={modeOptions}
            selectedOption={settings.scale_mode}
            onChange={(o) => {
              if (o.data === "system") {
                // go straight to the list of systems
                showModal(<SystemPicker systems={systems} current={settings.system} live={effectLive(settings)} />);
              } else {
                change({ scale_mode: o.data });
              }
            }}
          />
        </PanelSectionRow>
        {settings.scale_mode === "system" && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={() => showModal(<SystemPicker systems={systems} current={settings.system} live={effectLive(settings)} />)}
            >
              {(() => {
                const s = systems.find((x) => x.id === settings.system);
                return s ? systemLabel(s) : "Choose System …";
              })()}
            </ButtonItem>
          </PanelSectionRow>
        )}
        {settings.scale_mode === "system" && (
          <PanelSectionRow>
            <Dropdown
              menuLabel="Pixel size values"
              rgOptions={[
                { data: 720, label: "720p" },
                { data: 800, label: "800p" },
              ]}
              selectedOption={settings.game_height === 800 ? 800 : 720}
              onChange={(o) => change({ game_height: o.data })}
            />
          </PanelSectionRow>
        )}
        {settings.scale_mode === "system" && (
          <PanelSectionRow>
            <div style={{ fontSize: "12px", opacity: 0.7 }}>
              Try both options and see what looks better for each game/shader
            </div>
          </PanelSectionRow>
        )}
        {settings.scale_mode === "manual" && (
          <PanelSectionRow>
            <SliderField
              label="Pixel size"
              description="Screen pixels per original pixel"
              min={1}
              max={8}
              step={0.01}
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
