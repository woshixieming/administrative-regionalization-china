"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { dataVersion, loadMeta, loadProvinces } from "@/lib/data";
import type { Division, Meta, Village, LevelKey, Selection, SelectionCodes } from "@/lib/types";
import { LEVELS, resolveSelection, selectionCodes, selectionPath, selectionUrl } from "@/lib/selection";
import { catalogPath, locationSelection, lookupCode, pathCodes } from "@/lib/catalog";
import GlobalSearch from "./GlobalSearch";
import SelectionActions from "./SelectionActions";
import VersionPanel from "./VersionPanel";

export default function Explorer() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [provinces, setProvinces] = useState<Division[]>([]);
  const [lists, setLists] = useState<Record<LevelKey, Array<Division | Village>>>({ province: [], city: [], area: [], street: [], village: [] });
  const [sel, setSel] = useState<Selection>({});
  const [showLegacy, setShowLegacy] = useState(true);
  const [loading, setLoading] = useState<LevelKey | null>(null);
  const [query, setQuery] = useState<Record<string, string>>({});
  const [codeInput, setCodeInput] = useState("");
  const [codeMsg, setCodeMsg] = useState("");
  const [error, setError] = useState("");
  const navigation = useRef(0);
  const invalidateNavigation = useCallback(() => { navigation.current++; }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([loadMeta(), loadProvinces()])
      .then(([m, p]) => { if (!cancelled) { setMeta(m); setProvinces(p); } })
      .catch(() => { if (!cancelled) setError("数据加载失败，请稍后重试"); });
    return () => { cancelled = true; invalidateNavigation(); };
  }, [invalidateNavigation]);

  const navigate = useCallback(async (codes: SelectionCodes, history: boolean, mode: "push" | "none" = "push") => {
    const ticket = ++navigation.current;
    const depth = LEVELS.filter(({ key }) => codes[key]).length;
    setLoading(LEVELS[Math.min(depth, 4)].key); setError(""); setCodeMsg("");
    try {
      const resolved = await resolveSelection(codes, provinces, history);
      if (ticket !== navigation.current) return false;
      setLists(resolved.lists); setSel(resolved.selection); setShowLegacy(history); setQuery({});
      if (mode === "push") {
        const url = selectionUrl(window.location.href, selectionCodes(resolved.selection), history);
        if (url !== window.location.href) window.history.pushState(null, "", url);
      }
      return true;
    } catch (err) {
      if (ticket === navigation.current) setError(err instanceof Error ? err.message : "数据加载失败，请重试");
      return false;
    } finally { if (ticket === navigation.current) setLoading(null); }
  }, [provinces]);

  useEffect(() => {
    if (!provinces.length || !meta) return;
    const restore = async () => {
      const ticket = ++navigation.current;
      try {
        const state = await locationSelection(new URL(window.location.href), provinces, dataVersion(meta));
        if (ticket !== navigation.current) return;
        void navigate(state.codes, state.showLegacy, "none");
      } catch (err) {
        if (ticket !== navigation.current) return;
        invalidateNavigation(); setSel({}); setLoading(null);
        setLists({ province: provinces, city: [], area: [], street: [], village: [] });
        setError(err instanceof Error ? err.message : "分享链接无效");
      }
    };
    void restore();
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, [meta, provinces, navigate, invalidateNavigation]);

  const pick = useCallback(async (level: LevelKey, item: Division | Village) => {
    const index = LEVELS.findIndex((lv) => lv.key === level);
    const codes = Object.fromEntries(LEVELS.slice(0, index).map(({ key }) => [key, sel[key]!.code]));
    await navigate({ ...codes, [level]: item.code }, showLegacy);
  }, [navigate, sel, showLegacy]);

  const resolveCode = useCallback(async (input?: string) => {
    const raw = (input ?? codeInput).trim();
    if (!/^(?:\d{6}|\d{9}|\d{12})$/.test(raw)) { setCodeMsg("请输入 6、9 或 12 位数字行政区划代码"); return; }
    if (!meta) return;
    const ticket = ++navigation.current;
    setCodeMsg("正在定位…"); setLoading("province");
    try {
      const code = raw.padEnd(12, "0");
      const province = provinces.find((p) => p.code === code.slice(0, 2).padEnd(12, "0"));
      if (!province) throw new Error("未找到对应省级");
      const { catalog, node } = await lookupCode(province.code, code, dataVersion(meta));
      if (ticket !== navigation.current) return;
      if (!node) throw new Error("未找到该代码，请核对代码或尝试名称搜索");
      const path = catalogPath(catalog, node);
      if (!showLegacy && path.some((n) => n.legacy)) throw new Error("该代码属于历史补层，请开启“显示历史补层”后定位");
      if (await navigate(pathCodes(path), showLegacy)) setCodeMsg(`已定位到${LEVELS[node.level - 1].label}`);
    } catch (err) {
      if (ticket === navigation.current) { setCodeMsg(err instanceof Error ? err.message : "定位失败，请重试"); setLoading(null); }
    }
  }, [codeInput, meta, provinces, navigate, showLegacy]);

  const path = selectionPath(sel);
  const codes = selectionCodes(sel);
  const visibleLists = Object.fromEntries(LEVELS.map(({ key }) => [key,
    lists[key].filter((item) => showLegacy || !("legacy" in item && item.legacy)),
  ])) as typeof lists;

  function toggleLegacy() {
    const history = !showLegacy;
    const retained: SelectionCodes = {};
    for (const node of path) {
      if (!history && node.legacy) break;
      retained[LEVELS[node.level - 1].key] = node.code;
    }
    void navigate(retained, history);
  }

  const selectedCode: Record<LevelKey, string | undefined> = {
    province: sel.province?.code,
    city: sel.city?.code,
    area: sel.area?.code,
    street: sel.street?.code,
    village: sel.village?.code,
  };

  return (
    <main className="explorer-page">
      <header className="page-header">
        <div className="page-brand">
          <span className="brand-mark" aria-hidden="true">舆图</span>
          <div>
            <h1>中国行政区划</h1>
            <p>省、地、县、乡、村 · 五级数据浏览</p>
          </div>
        </div>

        <div className="source-overview">
          <SourcePill
            live={!!meta?.levels["4"]?.live}
            title={meta ? `省 / 地 / 县 / 乡 · ${shortSource(meta.sources[0]?.name)}` : "省 / 地 / 县 / 乡"}
            detail={
              meta
                ? `${meta.levels["4"]?.asOf ?? "—"} · ${
                    meta.levels["4"]?.live ? "官方活源" : "第三方整理"
                  }`
                : "加载中"
            }
          />
          <SourcePill
            live={false}
            title="村级 · 国家统计局"
            detail={meta ? `${meta.levels["5"]?.asOf ?? "—"} · 冻结快照` : "加载中"}
          />
        </div>
      </header>

      <section className="summary-strip" aria-label="全国行政区划数据总览">
        {LEVELS.map((lv, index) => {
          const info = meta?.levels[String(index + 1)];
          return (
            <div className="summary-item" key={lv.key}>
              <div className="summary-label">
                <span>{lv.label}</span>
                <span className={`status-dot ${info?.live ? "is-live" : "is-frozen"}`} />
              </div>
              <div className="summary-number">{info ? info.count.toLocaleString("zh-CN") : "—"}</div>
              <span className="summary-note">{index === 4 ? "2023 年冻结快照" : "建制记录（不含容器）"}</span>
              {!!info?.virtualCount && <span className="summary-extra">另有 {info.virtualCount} 个容器</span>}
              {!!info?.legacyCount && <span className="summary-extra">另有 {info.legacyCount.toLocaleString("zh-CN")} 个历史补层</span>}
            </div>
          );
        })}
      </section>

      {error && (
        <div role="alert" className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <VersionPanel meta={meta} onLocate={(code) => { setCodeInput(code); void resolveCode(code); }} />

      <GlobalSearch provinces={provinces} meta={meta} showLegacy={showLegacy}
        onSelect={async (target) => { await navigate(target, showLegacy); }} />

      <div className="explorer-toolbar">
        <div className="explorer-heading">
          <h2>区划浏览</h2>
          <p>逐级选择，查看下属行政区划</p>
        </div>
        <label className="history-toggle"><input type="checkbox" checked={showLegacy} disabled={loading !== null} onChange={toggleLegacy} />显示历史补层</label>
        <div className="code-search">
          <label htmlFor="division-code"><Icon name="search" /><span className="sr-only">行政区划代码定位</span></label>
          <input
            id="division-code"
            value={codeInput}
            onChange={(e) => setCodeInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && resolveCode()}
            placeholder="输入 6、9 或 12 位行政区划代码"
            inputMode="numeric"
            autoComplete="off"
            aria-describedby={codeMsg ? "code-message" : undefined}
          />
          <button onClick={() => void resolveCode()} type="button">
            定位<Icon name="arrow" />
          </button>
        </div>
      </div>
      {codeMsg && <p id="code-message" role="status" className="code-message">{codeMsg}</p>}

      <nav className="selection-path" aria-label="当前行政区划路径">
        <span className="path-label"><Icon name="location" />当前路径</span>
        <div className="path-items">
          {path.length === 0 ? (
            <span className="path-placeholder">从下方选择省级行政区，开始浏览</span>
          ) : (
            path.map((p, i) => (
              <span key={i} className={i === path.length - 1 ? "path-current" : ""}>
                {i > 0 && <Icon name="chevron" />}
                <button type="button" onClick={() => void pick(LEVELS[i].key, p)}>{p.name}</button>
              </span>
            ))
          )}
        </div>
        <span className="path-progress">已选 {path.length} / 5 级</span>
      </nav>

      <SelectionActions path={path} codes={codes} meta={meta} showLegacy={showLegacy} />

      <div className="division-grid" role="region" aria-label="五级行政区划列表" tabIndex={0}>
        {LEVELS.map((lv, index) => (
          <Column
            key={lv.key}
            step={index + 1}
            label={lv.label}
            hint={lv.hint}
            asOf={meta?.levels[String(index + 1)]?.asOf ?? ""}
            live={meta?.levels[String(index + 1)]?.live ?? true}
            items={visibleLists[lv.key]}
            selectedCode={selectedCode[lv.key]}
            loading={loading === lv.key}
            disabled={!enabled(lv.key, sel)}
            query={query[lv.key] ?? ""}
            onQuery={(v) => setQuery((q) => ({ ...q, [lv.key]: v }))}
            onPick={(it) => pick(lv.key, it)}
          />
        ))}
      </div>

      <footer className="data-footer">
        {meta ? (
          <>
            <p><span className="footer-tag">4 + 1 数据口径</span>前四级与村级采用不同来源及截止日期，村级保留 2023 年历史快照。</p>
            <div className="footer-detail">
              <p>历史补层：{meta.legacy.areas.toLocaleString("zh-CN")} 个县级、{meta.legacy.streets.toLocaleString("zh-CN")} 个乡级，仅用于挂载历史村级数据，不代表当前建制。</p>
              <span>数据生成于 {meta.generatedAt}</span>
            </div>
            <details className="source-details">
              <summary>查看数据来源与截止日期</summary>
              {meta.sources.map((s) => <p key={s.name}>{s.name} · {s.levels} 级 · 截止 {s.asOf}</p>)}
            </details>
          </>
        ) : (
          "加载中…"
        )}
      </footer>
    </main>
  );
}

function enabled(level: LevelKey, sel: Selection): boolean {
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
    <div className="source-line">
      <span className={`status-dot ${live ? "is-live" : "is-frozen"}`} />
      <span className="source-title">{title}</span>
      <span className="source-detail">{detail}</span>
    </div>
  );
}

function Column({
  step,
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
  step: number;
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
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (selectedCode) {
      listRef.current?.querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
    } else {
      listRef.current?.scrollTo({ top: 0 });
    }
  }, [selectedCode]);

  const filtered = useMemo(() => {
    const q = query.trim();
    if (!q) return items;
    return items.filter((i) => i.name.includes(q) || i.code.includes(q));
  }, [items, query]);

  return (
    <section className={`division-column ${disabled ? "is-disabled" : ""}`} aria-label={`${label}列表`}>
      <div className="column-header">
        <div className="column-title">
          <span className="column-step">{String(step).padStart(2, "0")}</span>
          <h3>{label}</h3>
          <span className="column-count">{items.length.toLocaleString("zh-CN")}</span>
        </div>
        <p className="column-hint" title={hint}>{hint}</p>
        <div className="column-search">
          <Icon name="search" />
          <input
            aria-label={`搜索${label}名称或代码`}
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder="搜索名称或代码"
            disabled={disabled}
          />
          {query && <button type="button" aria-label={`清空${label}搜索`} onClick={() => onQuery("")}><Icon name="close" /></button>}
        </div>
      </div>

      <div ref={listRef} className="column-list" aria-busy={loading}>
        {disabled ? (
          <div className="column-empty"><Icon name="layers" /><p>请先选择上一级</p><span>选择后查看{label}数据</span></div>
        ) : loading ? (
          <div className="column-empty" role="status"><span className="loading-spinner" /><p>正在加载数据…</p></div>
        ) : filtered.length === 0 ? (
          <div className="column-empty"><Icon name="search" /><p>{items.length === 0 ? "该级暂无数据" : "未找到匹配结果"}</p><span>{items.length === 0 ? "可尝试选择其他行政区划" : "请尝试其他名称或代码"}</span></div>
        ) : (
          <ul>
            {filtered.map((it) => {
              const active = it.code === selectedCode;
              const legacy = "legacy" in it && it.legacy;
              const virtual = "virtual" in it && it.virtual;
              return (
                <li key={it.code}>
                  <button
                    type="button"
                    aria-pressed={active}
                    onClick={() => onPick(it)}
                    className={`division-row ${active ? "is-selected" : ""}`}
                    title={`${it.name} · ${it.code}`}
                  >
                    <span className="division-row-content">
                      <span className="division-name">
                        {it.name}
                        {legacy && <span className="legacy-tag">历史</span>}
                        {virtual && <span className="container-tag">层级容器</span>}
                        {"carriedOver" in it && it.carriedOver && <span className="legacy-tag">沿用旧版</span>}
                        {"patchId" in it && it.patchId && <span className="legacy-tag">人工修订</span>}
                      </span>
                      <span className="division-code">{it.code}</span>
                    </span>
                    <Icon name="chevron" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <div className="column-footer">
        <span>{query ? `匹配 ${filtered.length} / ${items.length} 项` : `共 ${items.length.toLocaleString("zh-CN")} 项`}</span>
        {asOf && <span className={live ? "" : "frozen-date"}>{asOf}</span>}
      </div>
    </section>
  );
}

function Icon({ name }: { name: "layers" | "search" | "location" | "chevron" | "arrow" | "close" }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {name === "layers" && <><path d="m12 3 9 5-9 5-9-5 9-5Z" /><path d="m3 12 9 5 9-5M3 16l9 5 9-5" /></>}
      {name === "search" && <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></>}
      {name === "location" && <><path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 0 1 14 0Z" /><circle cx="12" cy="10" r="2.5" /></>}
      {name === "chevron" && <path d="m9 5 7 7-7 7" />}
      {name === "arrow" && <path d="M4 12h16m-6-6 6 6-6 6" />}
      {name === "close" && <path d="m6 6 12 12M6 18 18 6" />}
    </svg>
  );
}
