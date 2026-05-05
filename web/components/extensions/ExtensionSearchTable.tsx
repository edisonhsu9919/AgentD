"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import type { ExtensionSearchTableSchema } from "@/lib/types";

export default function ExtensionSearchTable({
  schema,
}: {
  schema: ExtensionSearchTableSchema;
}) {
  const [query, setQuery] = useState("");
  const normalizedQuery = query.trim().toLowerCase();

  const rows = useMemo(() => {
    if (!normalizedQuery) return schema.rows;
    return schema.rows.filter((row) =>
      Object.values(row).some((value) =>
        String(value ?? "").toLowerCase().includes(normalizedQuery),
      ),
    );
  }, [normalizedQuery, schema.rows]);

  return (
    <div className="mx-auto flex h-full w-full max-w-6xl flex-col gap-4 px-6 py-6">
      <section className="rounded-[28px] bg-white/72 p-6 shadow-[0_18px_48px_rgba(42,41,51,0.055)]">
        <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-text-secondary/60">
          Domain Extension
        </p>
        <h1 className="mt-2 text-2xl font-semibold tracking-[-0.03em] text-text-primary">
          {schema.title}
        </h1>
        {schema.description && (
          <p className="mt-3 max-w-3xl text-sm leading-6 text-text-secondary">
            {schema.description}
          </p>
        )}
      </section>

      <div className="flex min-w-0 items-center gap-2 rounded-full bg-bg-primary/65 px-4 py-2.5 shadow-[0_12px_32px_rgba(42,41,51,0.04)]">
        <Search size={15} className="text-text-secondary" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={schema.search_placeholder || "Search extension data..."}
          className="min-w-0 flex-1 bg-transparent text-sm text-text-primary outline-none placeholder:text-text-secondary/45"
        />
        {query && (
          <button
            type="button"
            onClick={() => setQuery("")}
            className="rounded-full px-2 text-sm text-text-secondary transition hover:bg-white/70 hover:text-text-primary"
          >
            &times;
          </button>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-hidden rounded-[28px] bg-white/72 p-3 shadow-[0_18px_48px_rgba(42,41,51,0.045)]">
        <div className="h-full overflow-auto">
          <table className="min-w-full border-separate border-spacing-y-1 text-left text-sm">
            <thead className="sticky top-0 z-10 bg-white/95 backdrop-blur">
              <tr>
                {schema.columns.map((column) => (
                  <th
                    key={column.key}
                    className="px-3 py-3 text-[11px] font-medium uppercase tracking-[0.12em] text-text-secondary/60"
                  >
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr
                  key={rowIndex}
                  className="rounded-[18px] transition hover:bg-bg-primary/70"
                >
                  {schema.columns.map((column) => (
                    <td
                      key={`${rowIndex}-${column.key}`}
                      className="max-w-[18rem] truncate px-3 py-3 text-text-primary"
                    >
                      {formatCell(row[column.key])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>

          {rows.length === 0 && (
            <div className="flex h-40 items-center justify-center text-sm text-text-secondary">
              没有匹配数据
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function formatCell(value: string | number | boolean | null) {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}
