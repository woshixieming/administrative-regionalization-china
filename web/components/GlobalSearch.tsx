"use client";

import { useEffect, useRef, useState } from "react";
import { pathCodes, searchRegions, type SearchHit } from "@/lib/catalog";
import { LEVELS, provenance } from "@/lib/selection";
import type { Division, Meta, SelectionCodes } from "@/lib/types";
import { dataVersion } from "@/lib/data";

export default function GlobalSearch({ provinces, meta, showLegacy, onSelect }: {
  provinces: Division[]; meta: Meta | null; showLegacy: boolean;
  onSelect: (codes: SelectionCodes) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [province, setProvince] = useState("");
  const [level, setLevel] = useState(0);
  const [result, setResult] = useState<{ hits: SearchHit[]; total: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const [page, setPage] = useState(0);
  const request = useRef<AbortController | null>(null);

  useEffect(() => {
    request.current?.abort();
    setBusy(false); setResult(null); setError(""); setProgress(""); setPage(0);
    return () => request.current?.abort();
  }, [query, province, level, showLegacy]);

  async function search() {
    request.current?.abort();
    if (query.trim().length < 2) { setError("请输入至少 2 个字或数字进行搜索"); return; }
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setError(""); setResult(null); setPage(0); setProgress("准备搜索…");
    try {
      const found = await searchRegions(query, province, level, showLegacy, controller.signal,
        (done, total) => { if (!controller.signal.aborted) setProgress(`已搜索 ${done} / ${total} 个省级区域`); });
      if (!meta || dataVersion(found) !== dataVersion(meta)) throw new Error("数据版本已更新，请刷新页面后重新搜索");
      if (!controller.signal.aborted) setResult(found);
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(err instanceof Error ? err.message : "搜索失败，请重试");
        controller.abort();
      }
    } finally {
      if (request.current === controller) { setBusy(false); setProgress(""); }
    }
  }

  return (
    <section className="global-search" aria-label="全国行政区划搜索">
      <div className="feature-heading"><div><h2>全局搜索</h2><p>搜索全国各级名称或代码，点击结果定位</p></div><span className="feature-note">{showLegacy ? "包含历史补层" : "已隐藏历史补层"}</span></div>
      <form className="global-search-form" onSubmit={(event) => { event.preventDefault(); void search(); }}>
        <input aria-label="全局搜索关键词" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="名称或代码前缀，例如：东城区、110101" />
        <select aria-label="搜索省份" value={province} onChange={(e) => setProvince(e.target.value)}><option value="">全国</option>{provinces.map((p) => <option key={p.code} value={p.code}>{p.name}</option>)}</select>
        <select aria-label="搜索层级" value={level} onChange={(e) => setLevel(Number(e.target.value))}><option value={0}>全部层级</option>{LEVELS.map((lv, i) => <option key={lv.key} value={i + 1}>{lv.label}</option>)}</select>
        <button type="submit" className="primary-button" disabled={busy || !meta}>{busy ? "搜索中…" : "搜索"}</button>
        {busy && <button type="button" className="secondary-button" onClick={() => { request.current?.abort(); setBusy(false); setProgress("搜索已取消"); }}>取消</button>}
      </form>
      <div aria-live="polite">{progress && <p className="feature-message">{progress}</p>}{error && <p className="feature-error" role="alert">{error}</p>}</div>
      {result && meta && <div className="search-results">
        <p className="result-summary" role="status">匹配 {result.total.toLocaleString("zh-CN")} 条{result.total > 200 ? "，展示前 200 条，请选择省份或层级缩小范围" : ""}</p>
        {result.total === 0 ? <p className="feature-message">暂无匹配记录，请尝试其他名称或代码。</p> : <>
          <ul>{result.hits.slice(page * 20, (page + 1) * 20).map((hit) => {
            const info = provenance(hit.node, meta);
            const history = hit.path.some((n) => n.legacy);
            return <li key={`${hit.node.level}:${hit.node.code}`}><button type="button" className="search-result" onClick={() => void onSelect(pathCodes(hit.path))}>
              <span className="result-title">{hit.node.name}<span className="result-level">{LEVELS[hit.node.level - 1].label}</span>{history && <span className="legacy-tag">含历史补层</span>}</span>
              <span className="result-path">{hit.path.map((n) => n.name).join(" / ")}</span>
              <span className="result-meta">{hit.node.code} · {info.status} · {info.source} · 截止 {info.asOf}</span>
            </button></li>;
          })}</ul>
          <div className="result-pagination"><span>第 {page + 1} / {Math.ceil(result.hits.length / 20)} 页</span><button type="button" className="secondary-button" disabled={page === 0} onClick={() => setPage((n) => n - 1)}>上一页</button><button type="button" className="secondary-button" disabled={(page + 1) * 20 >= result.hits.length} onClick={() => setPage((n) => n + 1)}>下一页</button><button type="button" className="text-button" onClick={() => setResult(null)}>收起结果</button></div>
        </>}
      </div>}
    </section>
  );
}
