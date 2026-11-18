import { useEffect, useRef } from "react";
import { useMutation } from "@tanstack/react-query";

// Per-call callbacks in TanStack Query belong to the latest observer only.
export function useHistoryDetail<K, V>(scope: string, load: (key: K) => Promise<V>, apply: (record: V) => void) {
  const generation = useRef(0);
  useEffect(() => () => { generation.current++; }, [scope]);
  const mutation = useMutation({ mutationFn: load });
  return { ...mutation, invalidate: () => { generation.current++; }, mutate(key: K) {
    const request = ++generation.current;
    mutation.mutate(key, { onSuccess(record) { if (request === generation.current) apply(record); } });
  } };
}

export async function deleteHistoryBatch(ids: string[], remove: (id: string) => Promise<unknown>) {
  const deleted: string[] = [];
  const failed: string[] = [];
  for (const id of new Set(ids)) {
    try { await remove(id); deleted.push(id); } catch { failed.push(id); }
  }
  return { deleted, failed };
}

export function checkDeletion(failed: string[]) {
  if (failed.length) throw new Error(`Failed to delete ${failed.length} history record(s).`);
}
