/**
 * 查询结果表格组件
 * 将后端返回的结构化数据归一化为可滚动表格
 */
import { Database, FileJson } from "lucide-react";
import type { ResultMeta } from "../types/agent";

function normalizeRows(data: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(data)) {
    return data.map((item, index) =>
      item && typeof item === "object" && !Array.isArray(item)
        ? (item as Record<string, unknown>)
        : { 序号: index + 1, 值: item },
    );
  }

  if (data && typeof data === "object") {
    return [data as Record<string, unknown>];
  }

  return [{ 值: data ?? "" }];
}

function formatCell(value: unknown) {
  if (value === null || value === undefined) return "-";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function ResultTable({ data, meta }: { data: unknown; meta?: ResultMeta }) {
  const rows = normalizeRows(data);
  const columns = Array.from(
    rows.reduce((keys, row) => {
      Object.keys(row).forEach((key) => keys.add(key));
      return keys;
    }, new Set<string>()),
  );

  if (columns.length === 0) {
    return null;
  }

  return (
    <section className="mt-4 overflow-hidden border border-ink/10 bg-white/70 shadow-line">
      <div className="flex items-center justify-between border-b border-ink/10 px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-ink">
          <Database className="h-4 w-4 text-moss" aria-hidden="true" />
          查询结果
        </div>
        <div className="flex items-center gap-2 text-xs text-ink/55">
          <FileJson className="h-3.5 w-3.5" aria-hidden="true" />
          {rows.length} 行
          {meta?.truncated && (
            <span className="border border-brass/40 bg-brass/15 px-1.5 py-0.5 text-ink/70">
              已截断，仅显示前 {meta.maxRows ?? rows.length} 行
            </span>
          )}
        </div>
      </div>
      {meta?.sql && (
        <details className="border-b border-ink/10 px-4 py-2 text-xs text-ink/60">
          <summary className="cursor-pointer select-none font-semibold">执行的 SQL</summary>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all font-mono text-[12px] leading-5 text-ink/75">
            {meta.sql}
          </pre>
        </details>
      )}
      <div className="max-h-[360px] overflow-auto">
        <table className="min-w-full border-separate border-spacing-0 text-left text-sm">
          <thead className="sticky top-0 z-10 bg-[#efe6d8]">
            <tr>
              {columns.map((column) => (
                <th
                  key={column}
                  scope="col"
                  className="border-b border-ink/10 px-4 py-3 font-semibold text-ink/70"
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex} className="odd:bg-white/45 even:bg-white/20">
                {columns.map((column) => (
                  <td key={column} className="border-b border-ink/5 px-4 py-3 text-ink/80">
                    {formatCell(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
