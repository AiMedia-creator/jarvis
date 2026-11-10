import { MemoryPanel } from "../components/MemoryPanel";
import { ViewFrame } from "./ViewFrame";

export default function MemoryView({ room, onBack }: { room: string; onBack: () => void }) {
  return (
    <ViewFrame className="memory-view" eyebrow="Gedächtnis" title="Was Jarvis behalten hat" onBack={onBack}>
      <MemoryPanel room={room} />
    </ViewFrame>
  );
}
