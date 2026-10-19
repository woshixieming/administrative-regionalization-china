"use client";

import { useEffect, useState } from "react";
import { loadSearchShard, loadSearchTop } from "@/lib/data";
import type { SearchEntry } from "@/lib/types";

const LEVEL_LABEL: Record<number, string> = { 1: "省", 2: "地", 3: "县", 4: "乡", 5: "村" };
const MAX_RESULTS = 50;

export default function SearchBox({ onPick }: { onPick: (code: string) => void }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<SearchEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const s = q.trim();
    if (!s) {
      setItems([]);
      setOpen(false);
      return;
    }
    let alive = true;
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        // L1–L3 常驻索引 + 该首字下的 L4–L5 分片
        const [top, shard] = await Promise.all([loadSearchTop(), loadSearchShard(s)]);
        if (!alive) return;
        const hit = [...top, ...shard].filter(
          ([name, code]) => name.includes(s) || code.startsWith(s),
        );
        // 前缀命中优先，其次名称短的优先
        hit.sort((a, b) => {
          const pa = a[0].startsWith(s) ? 0 : 1;
          const pb = b[0].startsWith(s) ? 0 : 1;
          return pa - pb || a[0].length - b[0].length;
        });
        setTotal(hit.length);
        setItems(hit.slice(0, MAX_RESULTS));
        setOpen(true);
      } catch {
        if (alive) setItems([]);
      } finally {
        if (alive) setLoading(false);
      }
    }, 250);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [q]);

  const pick = (code: string) => {
    setOpen(false);
    onPick(code);
  };

  return (
    <div
      className="relative"
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <div className="flex items-center gap-1.5">
        <span className="text-[12px] text-stone-500">搜索</span>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => items.length > 0 && setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && items.length) pick(items[0][1]);
            if (e.key === "Escape") setOpen(false);
          }}
          placeholder="直接搜村 / 街道 / 区县名称"
          className="w-52 rounded-md border border-stone-200 bg-stone-50 px-2.5 py-1 text-[13px] outline-none placeholder:text-stone-300 focus:border-stone-400"
        />
      </div>

      {open && (
        <div className="absolute left-0 top-full z-20 mt-1 max-h-80 w-80 overflow-y-auto rounded-lg border border-stone-200 bg-white py-1 shadow-sm">
          {loading && items.length === 0 ? (
            <p className="px-3 py-2 text-[12px] text-stone-400">检索中…</p>
          ) : items.length === 0 ? (
            <p className="px-3 py-2 text-[12px] text-stone-400">无匹配结果</p>
          ) : (
            <>
              {items.map(([name, code, level]) => (
                <button
                  key={code}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => pick(code)}
                  className="flex w-full items-baseline gap-1.5 px-3 py-1 text-left text-[12.5px] text-stone-700 hover:bg-stone-100"
                >
                  <span className="shrink-0 rounded bg-stone-100 px-1 text-[10px] text-stone-500">
                    {LEVEL_LABEL[level] ?? level}
                  </span>
                  <span className="truncate">{name}</span>
                  <span className="ml-auto shrink-0 font-mono text-[10px] text-stone-300">
                    {code}
                  </span>
                </button>
              ))}
              {total > items.length && (
                <p className="px-3 py-1.5 text-[11px] text-stone-400">
                  共 {total.toLocaleString()} 条，仅显示前 {MAX_RESULTS} 条，请输入更完整的名称
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
