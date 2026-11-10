import type { AudioEngine } from "../audio";
import type { Role } from "../types";

type Props = {
  audio: AudioEngine | null;
  role: Role;
  micOn: boolean;
  onMicChange: (active: boolean) => void;
  onLog: (message: string, level?: "info" | "warn") => void;
};

export function VoiceControls({
  audio,
  role,
  micOn,
  onMicChange,
  onLog,
}: Props) {
  const toggleMic = async () => {
    if (role !== "owner") {
      onLog("Mikrofon nur für OWNER verfügbar.", "warn");
      return;
    }
    if (!audio) {
      onLog("Audio-Kanal noch nicht bestätigt.", "warn");
      return;
    }
    try {
      if (!micOn) {
        await audio.enableMic();
        onMicChange(true);
      } else {
        audio.disableMic();
        onMicChange(false);
        onLog("Mikrofon deaktiviert; keine automatische Wiederaufnahme.");
      }
    } catch {
      onLog(
        "Mikrofonzugriff verweigert oder AudioWorklet nicht verfügbar.",
        "warn",
      );
    }
  };
  return (
    <button
      type="button"
      className={`btn mic-btn ${micOn ? "active" : ""}`}
      aria-pressed={micOn}
      aria-label={micOn ? "Mikrofon ausschalten" : "Mikrofon einschalten"}
      onClick={() => void toggleMic()}
    >
      {micOn ? "● Mikro an" : "○ Mikro"}
    </button>
  );
}
