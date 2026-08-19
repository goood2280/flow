import { EmptyState, ErrorState, LoadingState } from "./Feedback";

export function DataTable({
  columns = [],
  rows = [],
  rowKey = "id",
  loading = false,
  error = "",
  emptyTitle = "표시할 데이터가 없습니다",
  emptyMessage,
  caption,
  onRowClick,
  className = "",
}) {
  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} />;
  if (!rows.length) return <EmptyState title={emptyTitle} message={emptyMessage} />;

  return (
    <div className={`ds-table-frame${className ? ` ${className}` : ""}`}>
      <table className="ds-data-table">
        {caption && <caption className="u-sr-only">{caption}</caption>}
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={column.numeric ? "is-numeric" : ""} style={{ width: column.width }}>
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={row?.[rowKey] ?? index} onClick={onRowClick ? () => onRowClick(row) : undefined}>
              {columns.map((column) => (
                <td key={column.key} className={column.numeric ? "is-numeric" : ""}>
                  {column.render ? column.render(row) : row?.[column.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default DataTable;
