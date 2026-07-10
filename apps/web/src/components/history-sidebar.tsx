import { useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowRight, History, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { buttonClassName } from "@/components/ui/button-styles";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { useT } from "@/lib/i18n/context";

export function HistorySidebar<T>({
  items,
  itemId,
  activeId,
  title,
  emptyMessage,
  collapsed,
  onCollapsedChange,
  isLoading,
  isFetching = false,
  error,
  deletingId,
  bulkDeleting = false,
  onRefresh,
  onOpen,
  onDelete,
  onBulkDelete,
  canDelete = () => true,
  renderItem,
  className = "",
}: {
  items: T[];
  itemId: (item: T) => string;
  activeId?: string;
  title: string;
  emptyMessage: string;
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
  isLoading: boolean;
  isFetching?: boolean;
  error?: Error | null;
  deletingId?: string;
  bulkDeleting?: boolean;
  onRefresh: () => void;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
  onBulkDelete: (ids: string[]) => void;
  canDelete?: (item: T) => boolean;
  renderItem: (item: T, active: boolean) => ReactNode;
  className?: string;
}) {
  const t = useT();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [selecting, setSelecting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    const currentIds = new Set(items.map(itemId));
    setSelectedIds((previous) => {
      const next = new Set([...previous].filter((id) => currentIds.has(id)));
      return next.size === previous.size ? previous : next;
    });
  }, [items, itemId]);

  useEffect(() => {
    if (collapsed) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (window.innerWidth < 1024) return;
      if (event.target instanceof Node && rootRef.current?.contains(event.target)) return;
      onCollapsedChange(true);
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [collapsed, onCollapsedChange]);

  const toggleSelected = (id: string) => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (collapsed) {
    return (
      <div ref={rootRef} className={className}>
        <Card className="overflow-hidden">
          <div className="flex min-h-[72px] items-stretch gap-2 p-2 lg:min-h-[320px] lg:flex-col">
            <button
              type="button"
              className="group flex min-w-0 flex-1 items-center justify-between gap-3 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-left transition hover:border-primary/60 hover:bg-primary/10 focus:outline-none focus:ring-2 focus:ring-primary/30 lg:flex-col lg:justify-start lg:px-2 lg:py-3"
              aria-label={`${t("Open")} ${t(title)}`}
              onClick={() => onCollapsedChange(false)}
            >
              <span className="flex min-w-0 items-center gap-2 lg:flex-col">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-surface shadow-sm ring-1 ring-border transition group-hover:ring-primary/40">
                  <History className="h-4 w-4 text-primary" aria-hidden="true" />
                </span>
                <span className="block truncate text-sm font-semibold text-foreground lg:[text-orientation:mixed] lg:[writing-mode:vertical-rl]">
                  {t("History")}
                </span>
              </span>
              <span className="flex shrink-0 items-center gap-2 lg:mt-auto lg:flex-col">
                <Badge tone={items.length ? "info" : "neutral"}>{items.length}</Badge>
                <ArrowRight className="h-4 w-4 text-primary lg:rotate-90" aria-hidden="true" />
              </span>
            </button>
            <button
              type="button"
              className={buttonClassName("ghost")}
              aria-label={t("Refresh history")}
              title={t("Refresh history")}
              onClick={onRefresh}
            >
              <RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} aria-hidden="true" />
            </button>
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div ref={rootRef} className={className}>
      <Card className="min-w-0">
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <History className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
              <h2 className="truncate text-sm font-semibold">{t(title)}</h2>
              <Badge tone="info">{items.length}</Badge>
            </div>
            <div className="flex items-center gap-1">
              <button type="button" className={buttonClassName("ghost")} aria-label={t("Refresh history")} onClick={onRefresh}>
                <RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} aria-hidden="true" />
              </button>
              <button type="button" className={buttonClassName("ghost")} aria-label={t("Collapse history")} onClick={() => onCollapsedChange(true)}>
                <ArrowRight className="h-4 w-4 rotate-180" aria-hidden="true" />
              </button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {error ? (
            <div className="rounded-md border border-warning bg-warning/10 p-3 text-sm text-warning-foreground">
              {error.message}
            </div>
          ) : null}
          {isLoading ? (
            <div className="flex h-24 items-center justify-center text-sm text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin text-primary" aria-hidden="true" />
              {t("Loading history")}
            </div>
          ) : null}
          {!isLoading && !items.length ? (
            <div className="rounded-md border border-dashed border-border bg-muted/40 px-3 py-4 text-sm text-muted-foreground">
              {t(emptyMessage)}
            </div>
          ) : null}
          {items.length ? (
            <div className="flex items-center justify-between gap-2">
              <button
                type="button"
                className={buttonClassName("ghost")}
                onClick={() => {
                  setSelecting(!selecting);
                  setSelectedIds(new Set());
                }}
              >
                {t(selecting ? "Cancel" : "Select")}
              </button>
              {selecting ? (
                <button
                  type="button"
                  className={buttonClassName("secondary")}
                  disabled={!selectedIds.size || bulkDeleting}
                  onClick={() => {
                    const ids = [...selectedIds];
                    if (ids.length && window.confirm(t("Delete selected history items? This cannot be undone."))) {
                      onBulkDelete(ids);
                      setSelectedIds(new Set());
                      setSelecting(false);
                    }
                  }}
                >
                  {bulkDeleting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Trash2 className="h-4 w-4" aria-hidden="true" />}
                  {t("Delete selected")} {selectedIds.size ? `(${selectedIds.size})` : ""}
                </button>
              ) : null}
            </div>
          ) : null}
          <div className="max-h-72 space-y-2 overflow-y-auto pr-1 lg:max-h-[calc(100vh-220px)]">
            {items.map((item) => {
              const id = itemId(item);
              const active = id === activeId;
              const deletable = canDelete(item);
              return (
                <div
                  key={id}
                  className={[
                    "flex items-stretch gap-1 rounded-md border p-2 transition",
                    active
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border bg-surface text-foreground hover:border-primary/50 hover:bg-muted",
                  ].join(" ")}
                >
                  {selecting && deletable ? (
                    <input
                      type="checkbox"
                      className="mt-2 h-4 w-4 shrink-0 accent-primary"
                      checked={selectedIds.has(id)}
                      aria-label={`${t("Select")} ${id}`}
                      onChange={() => toggleSelected(id)}
                    />
                  ) : null}
                  <button
                    type="button"
                    className="min-w-0 flex-1 text-left"
                    onClick={() => {
                      onOpen(id);
                      onCollapsedChange(true);
                    }}
                  >
                    {renderItem(item, active)}
                  </button>
                  {!selecting && deletable ? (
                    <button
                      type="button"
                      className={[
                        "flex h-9 w-9 shrink-0 items-center justify-center rounded-md border transition",
                        active
                          ? "border-primary-foreground/30 text-primary-foreground/80 hover:bg-primary-foreground/10"
                          : "border-transparent text-muted-foreground hover:border-border hover:bg-surface hover:text-destructive",
                      ].join(" ")}
                      aria-label={t("Delete history item")}
                      disabled={deletingId === id}
                      onClick={() => {
                        if (window.confirm(t("Delete this history item? This cannot be undone."))) onDelete(id);
                      }}
                    >
                      {deletingId === id ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Trash2 className="h-4 w-4" aria-hidden="true" />}
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
