import type { ReactNode } from "react";

export function EmptyState({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex min-h-56 flex-col items-center justify-center rounded-lg border border-dashed border-border bg-muted/40 px-6 py-10 text-center">
      <h2 className="text-base font-semibold text-foreground">{title}</h2>
      <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">{detail}</p>
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}
