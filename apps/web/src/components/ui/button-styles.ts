import { cn } from "@/lib/cn";

export type ButtonVariant = "primary" | "secondary" | "ghost";

export function buttonClassName(variant: ButtonVariant = "primary", className?: string) {
  return cn(
    "inline-flex h-9 items-center justify-center gap-2 rounded-md border px-3 text-sm font-medium transition",
    "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
    "disabled:pointer-events-none disabled:opacity-50",
    variant === "primary" && "border-primary bg-primary text-primary-foreground hover:bg-primary/90",
    variant === "secondary" && "border-border bg-surface text-foreground hover:border-primary/40 hover:bg-muted",
    variant === "ghost" && "border-transparent bg-transparent text-muted-foreground hover:bg-muted hover:text-foreground",
    className,
  );
}
