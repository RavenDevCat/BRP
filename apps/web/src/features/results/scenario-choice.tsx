import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export function ScenarioChoice({ selected, onSelect, children }: {
  selected: boolean; onSelect: () => void; children: ReactNode;
}) {
  return <button type="button" aria-pressed={selected} onClick={onSelect}
    className={cn("min-w-0 rounded-md border p-3 text-left transition",
      selected ? "border-primary bg-primary/10" : "border-border bg-surface hover:bg-muted")}>
    {children}
  </button>;
}
