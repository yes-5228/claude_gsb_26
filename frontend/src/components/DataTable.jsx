import { useEffect, useRef } from 'react';

export default function DataTable({
  columns,
  rows,
  loading,
  error,
  emptyText = '暂无数据',
  rowKey,
  selectable = false,
  selectedIds,
  onToggleRow,
  onTogglePage,
}) {
  const headerCheckboxRef = useRef(null);

  const getKey = (row, index) => (rowKey ? rowKey(row) : (row.id ?? index));
  const pageKeys = rows.map((row, index) => getKey(row, index));
  const selectedSet = selectable ? new Set(selectedIds || []) : null;
  const selectedOnPage = pageKeys.filter((key) => selectedSet?.has(key)).length;
  const allPageSelected = selectable && rows.length > 0 && selectedOnPage === rows.length;
  const somePageSelected = selectable && selectedOnPage > 0 && !allPageSelected;

  useEffect(() => {
    if (headerCheckboxRef.current) {
      headerCheckboxRef.current.indeterminate = somePageSelected;
    }
  }, [somePageSelected]);

  if (loading) {
    return <div className="loading-block">数据加载中…</div>;
  }
  if (error) {
    return <div className="alert alert-error">{error.message || '数据加载失败'}</div>;
  }
  if (!rows.length) {
    return <div className="empty-block">{emptyText}</div>;
  }

  const selectionColumn = {
    key: '__selection__',
    title: '',
    width: '40px',
    render: (row, index) => (
      <input
        type="checkbox"
        aria-label="选择该行"
        checked={selectedSet.has(getKey(row, index))}
        onChange={() => onToggleRow?.(getKey(row, index))}
        onClick={(event) => event.stopPropagation()}
      />
    ),
  };
  const headerCheckbox = selectable ? (
    <input
      ref={headerCheckboxRef}
      type="checkbox"
      aria-label="全选本页"
      checked={allPageSelected}
      onChange={() => onTogglePage?.(!allPageSelected, pageKeys)}
    />
  ) : null;

  const renderedColumns = selectable ? [selectionColumn, ...columns] : columns;

  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {renderedColumns.map((column) => (
              <th key={column.key} style={column.width ? { width: column.width } : undefined}>
                {column.key === '__selection__' ? headerCheckbox : column.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={getKey(row, index)}
              className={selectable && selectedSet.has(getKey(row, index)) ? 'row-selected' : undefined}
            >
              {renderedColumns.map((column) => (
                <td key={column.key} className={column.wrap ? 'wrap' : undefined}>
                  {column.render ? column.render(row, index) : row[column.key] ?? '-'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
