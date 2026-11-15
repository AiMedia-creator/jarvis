import { useEffect, useRef, useState } from "react";
import { api, type Settings } from "./api";

export type VoicePrefs = { pause: number; barge: "frei" | "aus"; talkMode: "frei" | "ptt" };
export const DEFAULT_PREFS: VoicePrefs = { pause: 1.2, barge: "frei", talkMode: "frei" };
const KEY = "jarvis.cc.voice";

/** Browser-Einstellungen (nur Komfort): Sprechpause, Reinreden, Sprechmodus. Keine Gesprächsinhalte. */
export function loadPrefs(): VoicePrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "{}");
    return {
      pause: Number.isFinite(raw.pause) ? Math.max(0.5, Math.min(2.5, raw.pause)) : DEFAULT_PREFS.pause,
      barge: raw.barge === "aus" ? "aus" : "frei",
      talkMode: raw.talkMode === "ptt" ? "ptt" : "frei",
    };
  } catch {
    return DEFAULT_PREFS;
  }
}
export function savePrefs(prefs: VoicePrefs) {
  try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch { /* privates Fenster: gilt nur für diese Sitzung */ }
}

const MODEL_LABEL: Record<string, string> = { haiku: "Schnell (Haiku)", sonnet: "Ausgewogen (Sonnet)", opus: "Klug (Opus)" };
const TTS_LABEL: Record<string, string> = {
  eleven_flash_v2_5: "Flash – am schnellsten", eleven_v3_conversational: "v3 – am natürlichsten",
  eleven_turbo_v2_5: "Turbo – ausgewogen", eleven_multilingual_v2: "Multilingual v2 – hochwertig, langsamer",
};

export function SettingsDialog({ open, onClose, prefs, onPrefs, settings, onSettings, onReset, canEdit }: {
  open: boolean; onClose: () => void; prefs: VoicePrefs; onPrefs: (p: VoicePrefs) => void;
  settings: Settings | null; onSettings: (s: Settings) => void; onReset: () => void; canEdit: boolean;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const first = useRef<HTMLSelectElement>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  useEffect(() => {
    if (!open) return;
    setMessage(null);
    window.setTimeout(() => first.current?.focus(), 0);
  }, [open]);
  useEffect(() => () => audio.current?.pause(), []);
  if (!open) return null;

  const save = async (changes: Parameters<typeof api.saveSettings>[0], label: string) => {
    setBusy(label);
    setMessage(null);
    try {
      onSettings(await api.saveSettings(changes));
      setMessage({ tone: "ok", text: `${label} gespeichert.` });
    } catch (error) {
      setMessage({ tone: "err", text: `${label} nicht gespeichert: ${error instanceof Error ? error.message : "unbekannt"}` });
    } finally {
      setBusy(null);
    }
  };
  const preview = async () => {
    if (!settings) return;
    setBusy("Probehören");
    setMessage(null);
    try {
      const result = await api.ttsPreview("Guten Tag. So klinge ich mit dieser Stimme.", settings.stimme);
      audio.current?.pause();
      audio.current = new Audio(`data:${result.mime};base64,${result.audio}`);
      await audio.current.play();
    } catch (error) {
      setMessage({ tone: "err", text: `Probehören nicht möglich: ${error instanceof Error ? error.message : "unbekannt"}` });
    } finally {
      setBusy(null);
    }
  };
  const set = (patch: Partial<VoicePrefs>) => onPrefs({ ...prefs, ...patch });
  const disabled = !canEdit || !settings || busy !== null;

  return (
    <div className="cc-dialog-bg" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="cc-dialog" role="dialog" aria-modal="true" aria-labelledby="cc-settings-title"
        onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } }}>
        <header>
          <h2 id="cc-settings-title">Einstellungen</h2>
          <button type="button" className="cc-btn small" onClick={onClose}>Schließen <kbd>Esc</kbd></button>
        </header>
        {!settings && <p className="cc-empty">Einstellungen werden geladen … (Jarvis-Server muss laufen)</p>}
        {settings && (
          <div className="cc-form">
            {settings.sprachen && settings.sprache && (
              <label>Sprache
                <select value={settings.sprache} disabled={disabled} onChange={(e) => void save({ sprache: e.target.value }, "Sprache")}>
                  {settings.sprachen.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
                <span className="cc-hint" title="Automatisch erkennt Deutsch, Englisch oder Ägyptisch (Masri) und wechselt die Antwortsprache.">Gilt ab der nächsten Antwort, in Echtzeit ab dem nächsten Gespräch.</span>
              </label>
            )}
            {(settings.sprache === "arz" || settings.sprache === "auto") && settings.stimme_arabisch && (
              <label>Stimme für arabische Antworten
                <select value={settings.stimme_arabisch} disabled={disabled} onChange={(e) => void save({ stimme_arabisch: e.target.value }, "Arabische Stimme")}>
                  {settings.stimmen.map((v) => <option key={v.id} value={v.id}>{v.gruppe ? `${v.gruppe}: ` : ""}{v.name}</option>)}
                </select>
              </label>
            )}
            <label>Stimme
              <select ref={first} value={settings.stimme} disabled={disabled} onChange={(e) => void save({ stimme: e.target.value }, "Stimme")}>
                {settings.stimmen.map((v) => <option key={v.id} value={v.id}>{v.gruppe ? `${v.gruppe}: ` : ""}{v.name}</option>)}
              </select>
            </label>
            <button type="button" className="cc-btn small" disabled={disabled} onClick={() => void preview()}>▶ Probehören</button>
            {settings.elevenlabs_problem && <p className="cc-warn-text">{settings.elevenlabs_problem}</p>}
            <label>Sprechtempo <span className="mono">{settings.tempo.toFixed(2).replace(".", ",")}×</span>
              <input type="range" min={0.8} max={1.3} step={0.05} defaultValue={settings.tempo} disabled={disabled}
                onPointerUp={(e) => void save({ tempo: Number((e.target as HTMLInputElement).value) }, "Sprechtempo")}
                onKeyUp={(e) => { if (e.key.startsWith("Arrow")) void save({ tempo: Number((e.target as HTMLInputElement).value) }, "Sprechtempo"); }} />
            </label>
            <fieldset>
              <legend>Gehirn (Modell und Anbieter)</legend>
              {settings.modell_auswahl?.length ? (
                <ul className="cc-models" role="radiogroup" aria-label="Modell">
                  {settings.modell_auswahl.map((m) => (
                    <li key={m.key}>
                      <button type="button" role="radio" aria-checked={settings.modell_key === m.key}
                        disabled={disabled || (!m.verfuegbar && settings.modell_key !== m.key)}
                        className={settings.modell_key === m.key ? "on" : ""} onClick={() => void save({ modell: m.key }, "Modell")}>
                        <span><strong>{m.name}</strong><small>{m.anbieter_name}{m.modell && m.anbieter === "ollama" ? "" : ` · ${m.modell}`}</small></span>
                        {!m.verfuegbar && <em>{m.problem}</em>}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="cc-seg-group" role="radiogroup">
                  {settings.modelle.map((m) => (
                    <button key={m} type="button" role="radio" aria-checked={settings.modell_key === m} disabled={disabled}
                      className={settings.modell_key === m ? "on" : ""} onClick={() => void save({ modell: m }, "Modell")}>{MODEL_LABEL[m] ?? m}</button>
                  ))}
                </div>
              )}
              <span className="cc-hint">Aktuell: {settings.modell}{settings.anbieter ? ` (${settings.anbieter})` : ""}. Schlüssel liegen nur in Umgebungsvariablen, nie hier.</span>
              {settings.modell_auswahl?.length ? (() => {
                const main = settings.modell_auswahl.find((m) => m.key === settings.modell_key);
                const options = settings.modell_auswahl.filter((m) => m.key !== settings.modell_key && m.anbieter === main?.anbieter && m.verfuegbar && m.modell !== main?.modell);
                return (
                  <label>Backup bei Überlastung oder Ratenlimit
                    <select value={settings.backup_key ?? "aus"} disabled={disabled}
                      onChange={(e) => void save({ backup_modell: e.target.value }, "Backup")}>
                      <option value="aus">Kein Backup</option>
                      {options.map((m) => <option key={m.key} value={m.key}>{m.name}</option>)}
                    </select>
                    <span className="cc-hint">Springt nur ein, wenn das Hauptmodell überlastet ist oder bremst – für den Rest dieser Antwort.</span>
                  </label>
                );
              })() : null}
            </fieldset>
            {settings.erinnerungen_an_cloud !== undefined && (
              <fieldset className="cc-memory-privacy">
                <legend>Erinnerungen und Datenschutz</legend>
                <label className="cc-check">
                  <input type="checkbox" checked={settings.erinnerungen_an_cloud} disabled={disabled}
                    onChange={(e) => void save({ erinnerungen_an_cloud: e.target.checked }, "Erinnerungs-Einstellung")} />
                  <span>Passende Erinnerungen an den Cloud-Anbieter senden</span>
                </label>
                <span className="cc-hint">
                  {settings.anbieter_cloud === false
                    ? "Das aktive Modell läuft lokal (Ollama): Erinnerungen verlassen diesen Mac nicht."
                    : `Sonnet und Opus laufen bei ${settings.anbieter ?? "einem Cloud-Anbieter"}. Ist das Häkchen gesetzt, schickt Jarvis bei einer Frage höchstens fünf dazu passende Erinnerungen mit der Anfrage dorthin – nie den ganzen Speicher. Ohne Häkchen antwortet Jarvis ohne Erinnerungen; gespeichert bleibt alles lokal auf diesem Mac.`}
                </span>
              </fieldset>
            )}
            <label>Stimmqualität im Echtzeit-Modus
              <select value={settings.eleven_tts_model} disabled={disabled || !settings.eleven_tts_modelle.length}
                onChange={(e) => void save({ eleven_tts_model: e.target.value }, "Stimmqualität")}>
                {settings.eleven_tts_modelle.map((m) => <option key={m} value={m}>{TTS_LABEL[m] ?? m}</option>)}
              </select>
              <span className="cc-hint">Gilt ab dem nächsten Echtzeit-Gespräch.</span>
            </label>
            <label>Sprechpause, bis Jarvis antwortet <span className="mono">{prefs.pause.toFixed(1).replace(".", ",")} s</span>
              <input type="range" min={0.5} max={2.5} step={0.1} value={prefs.pause} onChange={(e) => set({ pause: Number(e.target.value) })} />
              <span className="cc-hint">Kürzer = schneller, länger = er fällt dir nicht ins Wort.</span>
            </label>
            <label>Reinreden, während Jarvis spricht (Studio)
              <select value={prefs.barge} onChange={(e) => set({ barge: e.target.value === "aus" ? "aus" : "frei" })}>
                <option value="frei">{settings.tuersteher?.aktiv ? "Frei – Sprechen stellt ihn leiser, der Türsteher entscheidet" : "Frei – Sprechen stoppt ihn sofort"}</option>
                <option value="aus">Aus – erst nach seiner Antwort zuhören</option>
              </select>
            </label>
            {settings.tuersteher && (
              <label className="cc-check">
                <input type="checkbox" checked={settings.tuersteher.an} disabled={disabled}
                  onChange={(e) => void save({ tuersteher: e.target.checked }, "Sprach-Türsteher")} />
                <span>Sprach-Türsteher (TypeSafe)
                  <span className="cc-hint">
                    {settings.tuersteher.problem
                      ? `Nicht aktiv: ${settings.tuersteher.problem}. Ohne Schlüssel antwortet Jarvis wie bisher auf alles.`
                      : settings.tuersteher.an
                        ? `Aktiv (${settings.tuersteher.modell}). Prüft jede gesprochene Äußerung: Fernseher, Gespräche im Raum und Kauderwelsch werden ignoriert, „Stopp“ hält Jarvis nur an. Gesendet werden nur die Äußerung und Jarvis' letzter Satz.`
                        : "Aus. Jarvis antwortet auf jede erkannte Äußerung und stoppt bei jedem Reinreden."}
                  </span>
                </span>
              </label>
            )}
            <label>Sprechmodus (Studio)
              <select value={prefs.talkMode} onChange={(e) => set({ talkMode: e.target.value === "ptt" ? "ptt" : "frei" })}>
                <option value="frei">Freihändig – einfach reden</option>
                <option value="ptt">Push-to-talk – Leertaste oder Mikro-Knopf gedrückt halten</option>
              </select>
            </label>
            <div className="cc-actions">
              <button type="button" className="cc-btn small" disabled={!canEdit} onClick={() => {
                if (window.confirm("Gespräch zurücksetzen? Jarvis vergisst den bisherigen Verlauf dieses Gesprächs (das Gedächtnis bleibt).")) onReset();
              }}>Gespräch zurücksetzen</button>
              <button type="button" className="cc-btn small" disabled={!canEdit || busy !== null} onClick={async () => {
                setBusy("Jobs");
                try {
                  const r = await api.stopAllJobs();
                  setMessage({ tone: "ok", text: r.abgebrochen.length ? `${r.abgebrochen.length} laufende Jobs gestoppt.` : "Keine laufenden Jobs." });
                } catch (error) {
                  setMessage({ tone: "err", text: `Stoppen fehlgeschlagen: ${error instanceof Error ? error.message : "unbekannt"}` });
                } finally { setBusy(null); }
              }}>Alle Jobs stoppen</button>
            </div>
          </div>
        )}
        {message && <p className={message.tone === "ok" ? "cc-ok-text" : "cc-warn-text"} role="status">{message.text}</p>}
      </div>
    </div>
  );
}
