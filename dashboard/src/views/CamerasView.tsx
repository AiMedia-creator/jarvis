import { CCTVGrid } from "../components/CCTVGrid";
import type { Camera } from "../types";
import { ViewFrame } from "./ViewFrame";

export default function CamerasView({
  cameras,
  selected,
  onSelect,
  demo,
  onBack,
}: {
  cameras: Camera[];
  selected: string | null;
  onSelect: (id: string) => void;
  demo: boolean;
  onBack: () => void;
}) {
  return (
    <ViewFrame
      className="cameras-view"
      eyebrow="Kameras"
      title={`${cameras.length} Quellen`}
      meta={demo ? "Simulation – keine echte Überwachungsquelle" : "Quellenstatus vom Server"}
      onBack={onBack}
    >
      <CCTVGrid cameras={cameras} selected={selected} onSelect={onSelect} />
    </ViewFrame>
  );
}
