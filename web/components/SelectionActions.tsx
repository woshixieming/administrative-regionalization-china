"use client";

import { useEffect, useRef, useState } from "react";
import { collectExport, downloadFile, toCsv } from "@/lib/export";
import { LEVELS, selectionUrl } from "@/lib/selection";
import type { Meta, RegionNode, SelectionCodes } from "@/lib/types";

export default function SelectionActions({ path, codes, meta, showLegacy }: {
  path: RegionNode[]; codes: SelectionCodes; meta: Meta | null; showLegacy: boolean;
}) {
  const [message, setMessage] = useState("");
  const [manual, setManual] = useState("");
  const [exporting, setExporting] = useState(false);
  const request = useRef<AbortController | null>(null);
  const scopeKey = path.map((node) => `${node.level}:${node.code}`).join("/");

  useEffect(() => {
    request.current?.abort(); setExporting(false); setMessage(""); setManual("");
    return () => request.current?.abort();
  }, [scopeKey, showLegacy]);

  async function copy(text: string, label: string) {
    setMessage(""); setManual("");
    try { await navigator.clipboard.writeText(text); setMessage(`${label}已复制`); }
    catch { setManual(text); setMessage("自动复制不可用，请选中下方内容手动复制"); }
  }

  async function exportData(format: "json" | "csv") {
    if (!meta) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setExporting(true); setMessage("正在准备所选区域及下级数据…");
    try {
      const data = await collectExport(path, meta, showLegacy, controller.signal);
      if (controller.signal.aborted) return;
      const anchor = path[path.length - 1];
      const name = `行政区划-${anchor.code}-${showLegacy ? "含历史" : "隐藏历史"}.${format}`;
      downloadFile(format === "json" ? JSON.stringify(data, null, 2) : toCsv(data.rows), name,
        format === "json" ? "application/json;charset=utf-8" : "text/csv;charset=utf-8");
      setMessage(`已导出 ${data.count.toLocaleString("zh-CN")} 条记录，包含来源和截止日期`);
    } catch (err) {
      if (!controller.signal.aborted) setMessage(err instanceof Error ? err.message : "导出失败，请重试");
    } finally { if (request.current === controller) setExporting(false); }
  }

  return <section className="selection-actions" aria-label="复制分享与导出">
    <div className="action-buttons">
      <button type="button" className="secondary-button" disabled={!path.length} onClick={() => void copy(path.filter((n) => !n.virtual).map((n) => n.name).join(" / "), "地址")}>复制地址</button>
      <button type="button" className="secondary-button" disabled={!path.length} onClick={() => void copy(path.map((n) => `${LEVELS[n.level - 1].label}：${n.code}`).join("\n"), "各级代码")}>复制代码</button>
      <button type="button" className="secondary-button" onClick={() => void copy(selectionUrl(window.location.href, codes, showLegacy), "分享链接")}>分享链接</button>
      <span className="action-divider" />
      <button type="button" className="secondary-button" disabled={!path.length || !meta || exporting} onClick={() => void exportData("json")}>导出 JSON</button>
      <button type="button" className="secondary-button" disabled={!path.length || !meta || exporting} onClick={() => void exportData("csv")}>导出 CSV</button>
      {exporting && <button type="button" className="text-button" onClick={() => { request.current?.abort(); setExporting(false); setMessage("导出已取消"); }}>取消导出</button>}
      <span className="feature-note">{path.length ? `导出范围：${path[path.length - 1].name}及下级` : "选择区域后可复制、导出"}</span>
    </div>
    {message && <p className="feature-message" role="status">{message}</p>}
    {manual && <textarea className="manual-copy" aria-label="手动复制内容" value={manual} readOnly onFocus={(e) => e.target.select()} />}
  </section>;
}
