import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Folder, FolderInput, FolderPlus, GitCompareArrows, History, Loader2, Pencil, RefreshCw, Trash2, UserPlus, Users, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { buttonClassName } from "@/components/ui/button-styles";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  assignHistoryGroup,
  deleteHistoryGroup,
  listHistoryGroups,
  moveHistoryGroupItems,
  removeHistoryGroupMember,
  renameHistoryGroup,
  setHistoryGroupMember,
  type HistoryGroupScope,
} from "@/lib/api";
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
  selectionActionLabel,
  selectionActionMin = 1,
  onSelectionAction,
  groupScope,
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
  selectionActionLabel?: string;
  selectionActionMin?: number;
  onSelectionAction?: (ids: string[]) => void;
  groupScope?: HistoryGroupScope;
  canDelete?: (item: T) => boolean;
  renderItem: (item: T, active: boolean) => ReactNode;
  className?: string;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [selecting, setSelecting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [managingGroupId, setManagingGroupId] = useState<string | null>(null);
  const [memberEmail, setMemberEmail] = useState("");
  const [memberRole, setMemberRole] = useState<"editor" | "viewer">("viewer");
  const groupsQuery = useQuery({
    queryKey: ["history-groups", groupScope],
    queryFn: () => listHistoryGroups(groupScope as HistoryGroupScope),
    enabled: Boolean(groupScope),
  });
  const assignGroupMutation = useMutation({
    mutationFn: ({ name, itemIds }: { name: string; itemIds: string[] }) => {
      if (!groupScope) throw new Error("History grouping is unavailable.");
      return assignHistoryGroup(groupScope, name, itemIds);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["history-groups", groupScope] });
      setSelectedIds(new Set());
      setSelecting(false);
    },
  });
  const renameGroupMutation = useMutation({
    mutationFn: ({ groupId, name }: { groupId: string; name: string }) => {
      if (!groupScope) throw new Error("History grouping is unavailable.");
      return renameHistoryGroup(groupScope, groupId, name);
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["history-groups", groupScope] }),
  });
  const moveGroupMutation = useMutation({
    mutationFn: ({ groupId, itemIds }: { groupId: string | null; itemIds: string[] }) => {
      if (!groupScope) throw new Error("History grouping is unavailable.");
      return moveHistoryGroupItems(groupScope, groupId, itemIds);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["history-groups", groupScope] });
      setSelectedIds(new Set());
      setSelecting(false);
    },
  });
  const memberMutation = useMutation({
    mutationFn: ({ groupId, email, role }: { groupId: string; email: string; role: "editor" | "viewer" }) => {
      if (!groupScope) throw new Error("History grouping is unavailable.");
      return setHistoryGroupMember(groupScope, groupId, email, role);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["history-groups", groupScope] });
      setMemberEmail("");
    },
  });
  const removeMemberMutation = useMutation({
    mutationFn: ({ groupId, email }: { groupId: string; email: string }) => {
      if (!groupScope) throw new Error("History grouping is unavailable.");
      return removeHistoryGroupMember(groupScope, groupId, email);
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["history-groups", groupScope] }),
  });
  const deleteGroupMutation = useMutation({
    mutationFn: (groupId: string) => {
      if (!groupScope) throw new Error("History grouping is unavailable.");
      return deleteHistoryGroup(groupScope, groupId);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["history-groups", groupScope] });
      setManagingGroupId(null);
    },
  });

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
  const selectedDeletableIds = items
    .filter((item) => selectedIds.has(itemId(item)) && canDelete(item))
    .map(itemId);
  const itemIds = new Set(items.map(itemId));
  const groups = (groupsQuery.data || [])
    .map((group) => ({
      ...group,
      item_ids: group.item_ids.filter((id) => itemIds.has(id)),
    }));
  const groupedIds = new Set(groups.flatMap((group) => group.item_ids));
  const ungroupedItems = items.filter((item) => !groupedIds.has(itemId(item)));
  const editableGroups = groups.filter((group) =>
    ["owner", "editor", "admin"].includes(group.role),
  );
  const groupByItem = new Map(
    groups.flatMap((group) => group.item_ids.map((id) => [id, group] as const)),
  );
  const groupError = (
    groupsQuery.error ||
    assignGroupMutation.error ||
    renameGroupMutation.error ||
    moveGroupMutation.error ||
    memberMutation.error ||
    removeMemberMutation.error ||
    deleteGroupMutation.error
  ) as Error | null;

  const renderHistoryItem = (item: T) => {
    const id = itemId(item);
    const active = id === activeId;
    const deletable = canDelete(item);
    const sourceGroup = groupByItem.get(id);
    const draggable = Boolean(
      groupScope &&
      !selecting &&
      (!sourceGroup || ["owner", "editor", "admin"].includes(sourceGroup.role)),
    );
    return (
      <div
        key={id}
        draggable={draggable}
        title={draggable ? t("Drag to move to another workspace") : undefined}
        onDragStart={(event) => {
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", id);
        }}
        className={[
          "flex items-stretch gap-1 rounded-md border p-2 transition",
          draggable ? "cursor-grab active:cursor-grabbing" : "",
          active
            ? "border-primary bg-primary text-primary-foreground"
            : "border-border bg-surface text-foreground hover:border-primary/50 hover:bg-muted",
        ].join(" ")}
      >
        {selecting && (deletable || onSelectionAction || groupScope) ? (
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
      <Card className="min-w-0 lg:flex lg:max-h-[calc(100vh-6rem)] lg:flex-col">
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
        <CardContent className="space-y-3 lg:flex lg:min-h-0 lg:flex-1 lg:flex-col lg:gap-3 lg:space-y-0">
          {error || groupError ? (
            <div className="rounded-md border border-warning bg-warning/10 p-3 text-sm text-warning-foreground">
              {(error || groupError)?.message}
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
                <div className="flex shrink-0 items-center gap-2">
                  {groupScope ? (
                    <button
                      type="button"
                      className={buttonClassName("secondary", "!h-10 !w-10 !px-0")}
                      aria-label={`${t("Group selected")}${selectedIds.size ? ` (${selectedIds.size})` : ""}`}
                      title={`${t("Group selected")}${selectedIds.size ? ` (${selectedIds.size})` : ""}`}
                      disabled={!selectedIds.size || assignGroupMutation.isPending}
                      onClick={() => {
                        const name = window.prompt(t("Group name"))?.trim();
                        if (name) {
                          assignGroupMutation.mutate({
                            name,
                            itemIds: [...selectedIds],
                          });
                        }
                      }}
                    >
                      {assignGroupMutation.isPending ? (
                        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                      ) : (
                        <FolderPlus className="h-4 w-4" aria-hidden="true" />
                      )}
                    </button>
                  ) : null}
                  {onSelectionAction && selectionActionLabel ? (
                    <button
                      type="button"
                      className={buttonClassName("primary", "!h-10 !w-10 !px-0")}
                      aria-label={`${t(selectionActionLabel)}${selectedIds.size ? ` (${selectedIds.size})` : ""}`}
                      title={`${t(selectionActionLabel)}${selectedIds.size ? ` (${selectedIds.size})` : ""}`}
                      disabled={selectedIds.size < selectionActionMin}
                      onClick={() => onSelectionAction([...selectedIds])}
                    >
                      <GitCompareArrows className="h-4 w-4" aria-hidden="true" />
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className={buttonClassName("secondary", "!h-10 !w-10 !px-0")}
                    aria-label={`${t("Delete selected")}${selectedDeletableIds.length ? ` (${selectedDeletableIds.length})` : ""}`}
                    title={`${t("Delete selected")}${selectedDeletableIds.length ? ` (${selectedDeletableIds.length})` : ""}`}
                    disabled={!selectedDeletableIds.length || bulkDeleting}
                    onClick={() => {
                      if (selectedDeletableIds.length && window.confirm(t("Delete selected history items? This cannot be undone."))) {
                        onBulkDelete(selectedDeletableIds);
                        setSelectedIds(new Set());
                        setSelecting(false);
                      }
                    }}
                  >
                    {bulkDeleting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Trash2 className="h-4 w-4" aria-hidden="true" />}
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}
          {selecting && groupScope ? (
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <FolderInput className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span className="sr-only">{t("Move selected to")}</span>
              <select
                className="h-9 min-w-0 flex-1 rounded-md border border-border bg-surface px-2 text-sm text-foreground"
                value=""
                disabled={!selectedIds.size || moveGroupMutation.isPending}
                aria-label={t("Move selected to")}
                onChange={(event) => {
                  const target = event.target.value;
                  if (!target) return;
                  moveGroupMutation.mutate({
                    groupId: target === "__ungrouped__" ? null : target,
                    itemIds: [...selectedIds],
                  });
                }}
              >
                <option value="">{t("Move selected to")}</option>
                <option value="__ungrouped__">{t("Ungrouped")}</option>
                {editableGroups.map((group) => (
                  <option key={group.group_id} value={group.group_id}>
                    {group.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <div className="max-h-72 space-y-2 overflow-y-auto pr-1 lg:min-h-0 lg:max-h-none lg:flex-1">
            {groups.map((group) => {
              const canEditGroup = ["owner", "editor", "admin"].includes(group.role);
              const canManageGroup = ["owner", "admin"].includes(group.role);
              return (
                <details
                  key={group.group_id}
                  className="relative overflow-hidden rounded-md border border-border bg-muted/30"
                  onDragOver={(event) => {
                    if (canEditGroup) event.preventDefault();
                  }}
                  onDrop={(event) => {
                    if (!canEditGroup) return;
                    event.preventDefault();
                    const id = event.dataTransfer.getData("text/plain");
                    if (id && groupByItem.get(id)?.group_id !== group.group_id) {
                      moveGroupMutation.mutate({ groupId: group.group_id, itemIds: [id] });
                    }
                  }}
                >
                  <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-3 py-2 pr-32 text-sm font-semibold hover:bg-muted [&::-webkit-details-marker]:hidden">
                    <Folder className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
                    <span className="min-w-0 flex-1 truncate">{group.name}</span>
                    <Badge tone="neutral">{group.item_ids.length}</Badge>
                    <span className="text-[11px] font-normal text-muted-foreground">
                      {t(group.role === "owner" ? "Owner" : group.role === "editor" ? "Editor" : group.role === "admin" ? "Admin" : "Viewer")}
                    </span>
                  </summary>
                  {canManageGroup ? (
                    <div className="absolute right-1 top-1 flex items-center">
                      <button
                        type="button"
                        className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-surface hover:text-foreground"
                        aria-label={t("Manage workspace members")}
                        title={t("Manage workspace members")}
                        onClick={(event) => {
                          event.preventDefault();
                          setManagingGroupId((current) => current === group.group_id ? null : group.group_id);
                          setMemberEmail("");
                        }}
                      >
                        <Users className="h-4 w-4" aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-surface hover:text-foreground"
                        aria-label={t("Rename workspace")}
                        title={t("Rename workspace")}
                        disabled={renameGroupMutation.isPending}
                        onClick={(event) => {
                          event.preventDefault();
                          const name = window.prompt(t("Workspace name"), group.name)?.trim();
                          if (name && name !== group.name) {
                            renameGroupMutation.mutate({ groupId: group.group_id, name });
                          }
                        }}
                      >
                        <Pencil className="h-4 w-4" aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-surface hover:text-destructive"
                        aria-label={t("Delete workspace")}
                        title={t("Delete workspace")}
                        disabled={deleteGroupMutation.isPending}
                        onClick={(event) => {
                          event.preventDefault();
                          if (window.confirm(t("Delete this workspace? Its tasks return to Ungrouped and are not deleted."))) {
                            deleteGroupMutation.mutate(group.group_id);
                          }
                        }}
                      >
                        <Trash2 className="h-4 w-4" aria-hidden="true" />
                      </button>
                    </div>
                  ) : null}
                  {managingGroupId === group.group_id && canManageGroup ? (
                    <div className="space-y-2 border-t border-border bg-surface p-2">
                      <div className="text-xs text-muted-foreground">
                        {t("Workspace owner")}: {group.owner_email}
                      </div>
                      <form
                        className="flex items-center gap-1"
                        onSubmit={(event) => {
                          event.preventDefault();
                          const email = memberEmail.trim();
                          if (email) memberMutation.mutate({ groupId: group.group_id, email, role: memberRole });
                        }}
                      >
                        <input
                          type="email"
                          className="h-9 min-w-0 flex-1 rounded-md border border-border bg-surface px-2 text-sm"
                          value={memberEmail}
                          placeholder={t("Member email")}
                          aria-label={t("Member email")}
                          onChange={(event) => setMemberEmail(event.target.value)}
                        />
                        <select
                          className="h-9 rounded-md border border-border bg-surface px-1 text-xs"
                          value={memberRole}
                          aria-label={t("Member role")}
                          onChange={(event) => setMemberRole(event.target.value as "editor" | "viewer")}
                        >
                          <option value="viewer">{t("Viewer")}</option>
                          <option value="editor">{t("Editor")}</option>
                        </select>
                        <button
                          type="submit"
                          className={buttonClassName("secondary", "!h-9 !w-9 !px-0")}
                          disabled={!memberEmail.trim() || memberMutation.isPending}
                          aria-label={t("Add or update member")}
                          title={t("Add or update member")}
                        >
                          {memberMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <UserPlus className="h-4 w-4" aria-hidden="true" />}
                        </button>
                      </form>
                      <div className="space-y-1">
                        {group.members.map((member) => (
                          <div key={member.member_email} className="flex min-w-0 items-center gap-2 text-xs">
                            <span className="min-w-0 flex-1 truncate">{member.member_email}</span>
                            <span className="text-muted-foreground">{t(member.role === "owner" ? "Owner" : member.role === "editor" ? "Editor" : "Viewer")}</span>
                            {member.role !== "owner" ? (
                              <button
                                type="button"
                                className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-destructive"
                                aria-label={t("Remove member")}
                                title={t("Remove member")}
                                onClick={() => removeMemberMutation.mutate({ groupId: group.group_id, email: member.member_email })}
                              >
                                <X className="h-3.5 w-3.5" aria-hidden="true" />
                              </button>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  <div className="space-y-2 border-t border-border p-2">
                    {group.item_ids.length ? (
                      items
                        .filter((item) => group.item_ids.includes(itemId(item)))
                        .map(renderHistoryItem)
                    ) : (
                      <div className="rounded-md border border-dashed border-border p-3 text-center text-xs text-muted-foreground">
                        {t(canEditGroup ? "Drop tasks here" : "No tasks in this workspace")}
                      </div>
                    )}
                  </div>
                </details>
              );
            })}
            <details
              open
              className="overflow-hidden rounded-md border border-dashed border-border bg-surface"
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                const id = event.dataTransfer.getData("text/plain");
                const source = groupByItem.get(id);
                if (id && source && ["owner", "editor", "admin"].includes(source.role)) {
                  moveGroupMutation.mutate({ groupId: null, itemIds: [id] });
                }
              }}
            >
              <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-semibold hover:bg-muted [&::-webkit-details-marker]:hidden">
                <FolderInput className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate">{t("Ungrouped")}</span>
                <Badge tone="neutral">{ungroupedItems.length}</Badge>
              </summary>
              <div className="space-y-2 border-t border-border p-2">
                {ungroupedItems.length ? ungroupedItems.map(renderHistoryItem) : (
                  <div className="rounded-md border border-dashed border-border p-3 text-center text-xs text-muted-foreground">
                    {t("Drop tasks here")}
                  </div>
                )}
              </div>
            </details>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
