import { useCallback, useMemo, useState } from 'react';

/**
 * 跨页勾选：选中集合独立于当前页数据保存，翻页、刷新本页后仍保留。
 * 同时维护勾选条目的快照（id -> row），批量弹窗可展示状态分布、
 * 提交前能再次核对数量。
 */
export function useRowSelection() {
  const [snapshot, setSnapshot] = useState(() => new Map());

  const selectedIds = useMemo(() => [...snapshot.keys()].sort((a, b) => a - b), [snapshot]);

  const toggleRow = useCallback((row) => {
    setSnapshot((prev) => {
      const next = new Map(prev);
      if (next.has(row.id)) next.delete(row.id);
      else next.set(row.id, row);
      return next;
    });
  }, []);

  // 按 ID 切换（表格复选框只拿到 id），行数据用于建立快照
  const toggleRowById = useCallback((id, rowFallback) => {
    setSnapshot((prev) => {
      const next = new Map(prev);
      if (next.has(id)) next.delete(id);
      else next.set(id, rowFallback || { id });
      return next;
    });
  }, []);

  const togglePage = useCallback((checked, pageRows) => {
    setSnapshot((prev) => {
      const next = new Map(prev);
      pageRows.forEach((row) => {
        if (checked) next.set(row.id, row);
        else next.delete(row.id);
      });
      return next;
    });
  }, []);

  const pruneMissing = useCallback((existingIds) => {
    setSnapshot((prev) => {
      const keep = new Set(existingIds);
      const next = new Map();
      prev.forEach((row, id) => {
        if (keep.has(id)) next.set(id, row);
      });
      return next.size === prev.size ? prev : next;
    });
  }, []);

  const clear = useCallback(() => setSnapshot(new Map()), []);

  return {
    selectedIds,
    selectedRows: selectedIds.map((id) => snapshot.get(id)),
    count: snapshot.size,
    toggleRow,
    toggleRowById,
    togglePage,
    pruneMissing,
    clear,
  };
}
