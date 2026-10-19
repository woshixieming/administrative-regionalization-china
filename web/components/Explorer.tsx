"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  loadAreas,
  loadCities,
  loadMeta,
  loadProvinces,
  loadStreets,
  loadVillages,
} from "@/lib/data";
import type { Division, Meta, Village } from "@/lib/types";
import SearchBox from "./SearchBox";

type Sel = { province?: Division; city?: Division; area?: Division; street?: Division; village?: Village };

const LEVELS = [
  { key: "province", label: "省级", hint: "省 / 自治区 / 直辖市" },
  { key: "city", label: "地级", hint: "地级市 / 地区 / 自治州 / 盟" },
  { key: "area", label: "县级", hint: "区 / 县 / 县级市 / 旗" },
  { key: "street", label: "乡级", hint: "街道 / 镇 / 乡 / 苏木" },
  { key: "village", label: "村级", hint: "社区居委会 / 村委会 / 嘎查" },
] as const;

type LevelKey = (typeof LEVELS)[number]["key"];

export default function Explorer() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [provinces, setProvinces] = useState<Division[]>([]);
  const [cities, setCities] = useState<Division[]>([]);
  const [areas, setAreas] = useState<Division[]>([]);
  const [streets, setStreets] = useState<Division[]>([]);
  const [villages, setVillages] = useState<Village[]>([]);
  const [sel, setSel] = useState<Sel>({});
  const [loading, setLoading] = useState<LevelKey | null>(null);
  const [query, setQuery] = useState<Record<string, string>>({});
  const [codeInput, setCodeInput] = useState("");
  const [codeMsg, setCodeMsg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([loadMeta(), loadProvinces()])
      .then(([m, p]) => {
        setMeta(m);
        setProvinces(p);
      })
      .catch(() => setError("数据加载失败，请确认已执行 scripts/build_dataset.py"));
  }, []);

  const pick = useCallback(
    async (level: LevelKey, item: Division | Village) => {
      if (level === "province") {
        setSel({ province: item as Division });
        setCities([]); setAreas([]); setStreets([]); setVillages([]);
        setLoading("city");
        try {
          setCities((await loadCities(item.code)) as Division[]);
        } catch { setError("地级数据加载失败"); }
        setLoading(null);
      } else if (level === "city") {
        setSel((s) => ({ ...s, city: item as Division }));
        setAreas([]); setStreets([]); setVillages([]);
        setLoading("area");
        try {
          setAreas((await loadAreas(item.code)) as Division[]);
        } catch { setError("县级数据加载失败"); }
        setLoading(null);
      } else if (level === "area") {
        setSel((s) => ({ ...s, area: item as Division }));
        setStreets([]); setVillages([]);
        setLoading("street");
        try {
          setStreets((await loadStreets(item.code)) as Division[]);
        } catch { setError("乡级数据加载失败"); }
        setLoading(null);
      } else if (level === "street") {
        setSel((s) => ({ ...s, street: item as Division }));
        setVillages([]);
        setLoading("village");
        try {
          setVillages(await loadVillages(item.code));
        } catch { setError("村级数据加载失败"); }
        setLoading(null);
      } else {
        setSel((s) => ({ ...s, village: item as Village }));
      }
    },
    [],
  );

  /** 12 位代码逐级定位 */
  const locateByCode = useCallback(
    async (input: string) => {
    const raw = input.trim();
    setCodeMsg("");
    if (!/^\d{6,12}$/.test(raw)) {
      setCodeMsg("请输入 6–12 位数字行政区划代码");
      return;
    }
    const c = raw.padEnd(12, "0");
    try {
      const pc = c.slice(0, 2).padEnd(12, "0");
      const p = provinces.find((x) => x.code === pc);
      if (!p) return setCodeMsg("未找到对应省级");
      await pick("province", p);

      const cityList = (await loadCities(pc)) as Division[];
      const cc = c.slice(0, 4).padEnd(12, "0");
      // 直辖市无地级层，直接用省码作为容器
      const city = cityList.find((x) => x.code === cc) ?? cityList[0];
      if (!city) return setCodeMsg("未找到对应地级");
      await pick("city", city);

      const areaList = (await loadAreas(city.code)) as Division[];
      const ac = c.slice(0, 6).padEnd(12, "0");
      const area = areaList.find((x) => x.code === ac);
      if (!area) return setCodeMsg("已定位到县级列表，未找到该县级代码");
      await pick("area", area);

        if (raw.length >= 9) {
          const streetList = (await loadStreets(area.code)) as Division[];
          const sc = c.slice(0, 9).padEnd(12, "0");
          const street = streetList.find((x) => x.code === sc);
          if (!street) return setCodeMsg("已定位到乡级列表，未找到该乡级代码");
          await pick("street", street);

          // 乡级码末 3 位为 000，表示只定位到乡级，不必再找村级
          if (raw.length === 12 && c.slice(9) !== "000") {
            const vList = await loadVillages(street.code);
            const v = vList.find((x) => x.code === c);
            if (v) {
              setSel((s) => ({ ...s, village: v }));
              setCodeMsg("已定位到村级");
            } else setCodeMsg("已定位到乡级，未找到该村级代码");
          } else setCodeMsg("已定位到乡级");
        } else setCodeMsg("已定位到县级");
      } catch {
        setCodeMsg("定位过程中数据加载失败");
      }
    },
    [provinces, pick],
  );

  const resolveCode = useCallback(() => locateByCode(codeInput), [codeInput, locateByCode]);

  // 首次加载后从 URL 恢复选择路径，使链接可分享、刷新不丢
  const restored = useRef(false);
  useEffect(() => {
    if (!provinces.length || restored.current) return;
    restored.current = true;
    const c = new URLSearchParams(window.location.search).get("code");
    if (!c) return;
    setCodeInput(c);
    void locateByCode(c);
  }, [provinces, locateByCode]);

  // 选择路径写回 URL（replaceState，避免在历史里堆大量记录）
  useEffect(() => {
    const deepest =
      sel.village?.code ??
      sel.street?.code ??
      sel.area?.code ??
      sel.city?.code ??
      sel.province?.code;
    const url = new URL(window.location.href);
    if (deepest) url.searchParams.set("code", deepest);
    else url.searchParams.delete("code");
    window.history.replaceState(null, "", url.toString());
  }, [sel]);

  const lists: Record<LevelKey, Array<Division | Village>> = {
    province: provinces,
    city: cities,
    area: areas,
    street: streets,
    village: villages,
  };

  const selectedCode: Record<LevelKey, string | undefined> = {
    province: sel.province?.code,
    city: sel.city?.code,
    area: sel.area?.code,
    street: sel.street?.code,
    village: sel.village?.code,
  };

  const path = [sel.province, sel.city, sel.area, sel.street, sel.village].filter(Boolean);

  return (
    <div className="mx-auto max-w-[1500px] px-4 py-5 sm:px-6">
      <header className="mb-4">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-[19px] font-medium tracking-tight text-stone-900">
            中国行政区划 · 五级数据浏览
          </h1>
          <span className="text-[12px] text-stone-400">4 + 1 数据口径</span>
        </div>

        <div className="mt-2.5 flex flex-wrap gap-2">
          <SourcePill
            live={!!meta?.levels["4"]?.live}
            title={meta ? `省 / 地 / 县 / 乡 · ${shortSource(meta.sources[0]?.name)}` : "省 / 地 / 县 / 乡"}
            detail={
              meta
                ? `截止 ${meta.levels["4"].asOf} · ${
                    meta.levels["4"].live ? "官方活源" : "第三方整理（非一手接口）"
                  }`
                : "加载中"
            }
          />
          <SourcePill
            live={false}
            title="村级 · 国家统计局"
            detail={meta ? `截止 ${meta.levels["5"].asOf} · 源已停发，冻结基线` : "加载中"}
          />
        </div>

        {meta && (
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px]">
            {Object.entries(meta.levels).map(([k, v]) => (
              <span key={k} className="flex items-baseline gap-1">
                <span className="text-stone-400">{v.label}</span>
                <span className="font-medium tabular-nums text-stone-800">
                  {v.count.toLocaleString()}
                </span>
                <span className={v.live ? "text-emerald-500" : "text-amber-500"}>
                  {v.live ? "●" : "○"}
                </span>
              </span>
            ))}
            {meta.legacy.streets > 0 && (
              <span className="text-stone-400">
                含历史补层 {meta.legacy.streets.toLocaleString()} 个乡级
              </span>
            )}
          </div>
        )}
      </header>

      {error && (
        <div className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-700">
          {error}
        </div>
      )}

      <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-stone-200 bg-white px-3 py-2">
        <SearchBox
          onPick={(code) => {
            setCodeInput(code);
            void locateByCode(code);
          }}
        />

        <div className="flex items-center gap-2">
          <span className="text-[12px] text-stone-500">代码定位</span>
          <input
            value={codeInput}
            onChange={(e) => setCodeInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && resolveCode()}
            placeholder="输入 6–12 位代码"
            className="w-44 rounded-md border border-stone-200 bg-stone-50 px-2.5 py-1 text-[13px] outline-none placeholder:text-stone-300 focus:border-stone-400"
          />
          <button
            onClick={resolveCode}
            className="rounded-md bg-stone-900 px-3 py-1 text-[12px] text-white hover:bg-stone-700"
          >
            定位
          </button>
        </div>
        {codeMsg && <span className="text-[12px] text-stone-500">{codeMsg}</span>}
        <div className="ml-auto flex items-center gap-1.5 text-[12px] text-stone-400">
          <span>当前路径</span>
          {path.length === 0 ? (
            <span className="text-stone-300">未选择</span>
          ) : (
            path.map((p, i) => (
              <span key={i} className="text-stone-600">
                {i > 0 && <span className="text-stone-300"> / </span>}
                {p!.name}
              </span>
            ))
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {LEVELS.map((lv) => (
          <Column
            key={lv.key}
            label={lv.label}
            hint={lv.hint}
            asOf={meta ? meta.levels[String(LEVELS.indexOf(lv) + 1)].asOf : ""}
            live={meta ? meta.levels[String(LEVELS.indexOf(lv) + 1)].live : true}
            items={lists[lv.key]}
            selectedCode={selectedCode[lv.key]}
            loading={loading === lv.key}
            disabled={!enabled(lv.key, sel)}
            query={query[lv.key] ?? ""}
            onQuery={(v) => setQuery((q) => ({ ...q, [lv.key]: v }))}
            onPick={(it) => pick(lv.key, it)}
          />
        ))}
      </div>

      <footer className="mt-4 rounded-xl border border-stone-200 bg-white px-4 py-3 text-[12px] leading-relaxed text-stone-500">
        {meta ? (
          <div className="flex flex-wrap gap-x-6 gap-y-1.5">
            {meta.sources.map((s) => (
              <span key={s.name}>
                <span className="text-stone-700">{s.name}</span>
                <span className="text-stone-400"> · {s.levels} 级 · 截止 {s.asOf}</span>
              </span>
            ))}
            <span className="text-stone-400">
              历史补层 {meta.legacy.streets} 个乡级 / {meta.legacy.areas} 个县级（
              {meta.legacy.note}）
            </span>
            <span className="text-stone-300">生成于 {meta.generatedAt}</span>
          </div>
        ) : (
          "加载中…"
        )}
      </footer>
    </div>
  );
}

function enabled(level: LevelKey, sel: Sel): boolean {
  if (level === "province") return true;
  if (level === "city") return !!sel.province;
  if (level === "area") return !!sel.city;
  if (level === "street") return !!sel.area;
  return !!sel.street;
}

function shortSource(name?: string): string {
  if (!name) return "未知来源";
  return name.split("（")[0].split(" · ")[0];
}

function SourcePill({ live, title, detail }: { live: boolean; title: string; detail: string }) {
  return (
    <span
      className={
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] " +
        (live
          ? "border-emerald-200 bg-emerald-50 text-emerald-800"
          : "border-amber-200 bg-amber-50 text-amber-800")
      }
    >
      <span className={"h-1.5 w-1.5 rounded-full " + (live ? "bg-emerald-500" : "bg-amber-500")} />
      <span className="font-medium">{title}</span>
      <span className={live ? "text-emerald-600" : "text-amber-600"}>{detail}</span>
    </span>
  );
}

function Column({
  label,
  hint,
  asOf,
  live,
  items,
  selectedCode,
  loading,
  disabled,
  query,
  onQuery,
  onPick,
}: {
  label: string;
  hint: string;
  asOf: string;
  live: boolean;
  items: Array<Division | Village>;
  selectedCode?: string;
  loading: boolean;
  disabled: boolean;
  query: string;
  onQuery: (v: string) => void;
  onPick: (it: Division | Village) => void;
}) {
  const filtered = useMemo(() => {
    const q = query.trim();
    if (!q) return items;
    return items.filter((i) => i.name.includes(q) || i.code.includes(q));
  }, [items, query]);

  return (
    <section className="flex min-h-[300px] flex-col rounded-xl border border-stone-200 bg-white">
      <div className="border-b border-stone-100 px-3 py-2">
        <div className="flex items-center justify-between">
          <span className="text-[13px] font-medium text-stone-800">{label}</span>
          <span className="text-[11px] text-stone-400">{items.length || ""}</span>
        </div>
        <div className="mt-0.5 flex items-center gap-1.5">
          <span className="text-[11px] text-stone-400">{hint}</span>
          {asOf && (
            <span
              className={
                "text-[10px] " + (live ? "text-emerald-600" : "text-amber-600")
              }
            >
              {asOf}
            </span>
          )}
        </div>
        <div className="relative mt-1.5">
          <input
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder="搜索名称或代码"
            disabled={disabled}
            className="w-full rounded-md border border-stone-200 bg-stone-50 px-2 py-1 text-[12px] outline-none placeholder:text-stone-300 focus:border-stone-400 disabled:opacity-40"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-1">
        {disabled ? (
          <p className="px-2 py-6 text-center text-[12px] text-stone-300">请先选择上一级</p>
        ) : loading ? (
          <p className="px-2 py-6 text-center text-[12px] text-stone-300">加载中…</p>
        ) : filtered.length === 0 ? (
          <p className="px-2 py-6 text-center text-[12px] text-stone-300">
            {items.length === 0 ? "该级无数据" : "无匹配结果"}
          </p>
        ) : (
          <ul>
            {filtered.map((it) => {
              const active = it.code === selectedCode;
              const legacy = "legacy" in it && it.legacy;
              return (
                <li key={it.code}>
                  <button
                    onClick={() => onPick(it)}
                    className={
                      "flex w-full items-baseline gap-1.5 rounded-md px-2 py-1 text-left text-[12.5px] transition-colors " +
                      (active
                        ? "bg-stone-900 text-white"
                        : "text-stone-700 hover:bg-stone-100")
                    }
                  >
                    <span className="truncate">{it.name}</span>
                    {legacy && (
                      <span
                        className={
                          "shrink-0 rounded px-1 text-[10px] " +
                          (active ? "bg-stone-700 text-stone-300" : "bg-amber-100 text-amber-700")
                        }
                      >
                        历史
                      </span>
                    )}
                    <span
                      className={
                        "ml-auto shrink-0 font-mono text-[10px] " +
                        (active ? "text-stone-400" : "text-stone-300")
                      }
                    >
                      {it.code.slice(0, 12)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
