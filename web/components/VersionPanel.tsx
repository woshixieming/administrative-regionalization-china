"use client";

import { useEffect, useState } from "react";
import { dataVersion } from "@/lib/data";
import { fetchJson } from "@/lib/catalog";
import { LEVELS } from "@/lib/selection";
import type { Meta } from "@/lib/types";

const KINDS = { added: "新增记录", removed: "撤销记录", renamed: "名称调整", reparented: "上级调整", statusChanged: "状态调整" };
type Kind = keyof typeof KINDS;
interface Summary { initial: boolean; previousVersion: string | null; previousGeneratedAt: string | null; counts: Record<Kind, Record<string, number>>; totalEvents: number; note: string }
interface Change { code: string; level: number; before: { name: string; parentCode: string } | null; after: { name: string; parentCode: string } | null }
interface Status { label?: string; status?: string; startedAt?: string; finishedAt?: string; lastSuccessAt?: string; datasetVersion?: string }
interface Patch { id: string; code: string; name: string; streetCode: string; reason: string; source: string; effectiveDate: string; applied: boolean }

function dateLabel(value?: string | null) {
  if (!value) return "暂无记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

export default function VersionPanel({ meta, onLocate }: { meta: Meta | null; onLocate: (code: string) => void }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"status" | "changes" | "patches">("status");
  const [status, setStatus] = useState<Status>({});
  const [latest, setLatest] = useState("");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [patches, setPatches] = useState<Patch[]>([]);
  const [kind, setKind] = useState<Kind>("added");
  const [level, setLevel] = useState(3);
  const [changes, setChanges] = useState<Change[]>([]);
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const version = meta ? dataVersion(meta) : "";

  useEffect(() => {
    if (!open || !version) return;
    const controller = new AbortController();
    async function refresh() {
      try {
        const [response, current] = await Promise.all([
          fetch("/api/status", { cache: "no-store", signal: controller.signal }),
          fetch("/api/data/meta.json", { cache: "no-store", signal: controller.signal }),
        ]);
        if (response.ok) setStatus(await response.json() as Status);
        if (current.ok) setLatest(dataVersion(await current.json() as Meta));
      } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "更新状态暂不可用"); }
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 60000);
    if (/^[a-f0-9]{24}$/.test(version)) {
      Promise.all([fetchJson<Summary>("changes/summary.json", controller.signal, version), fetchJson<{ records: Patch[] }>("patches.json", controller.signal, version)])
        .then(([report, ledger]) => { if (!controller.signal.aborted) { setSummary(report); setPatches(ledger.records); } })
        .catch((err) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "版本报告加载失败"); });
    }
    return () => { controller.abort(); clearInterval(timer); };
  }, [open, version]);

  useEffect(() => {
    if (!open || tab !== "changes" || !summary) return;
    const controller = new AbortController();
    setBusy(true); setError(""); setPage(0); setChanges([]);
    fetchJson<Change[]>(`changes/${kind}-${level}.json`, controller.signal, version)
      .then((rows) => { if (!controller.signal.aborted) setChanges(rows); })
      .catch((err) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "变更明细加载失败"); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [open, tab, summary, kind, level, version]);

  return <details className="version-panel" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>数据版本、更新状态与变更记录<span>{meta?.datasetVersion?.slice(0, 8) ?? "加载中"}</span></summary>
    {open && <div className="version-content">
      <div className="version-tabs" role="tablist" aria-label="数据维护信息">
        {([["status", "更新状态"], ["changes", "版本变化"], ["patches", "人工修订"]] as const).map(([key, label]) => <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{label}</button>)}
      </div>
      {error && <p role="alert" className="feature-error">{error}</p>}
      {latest && latest !== version && <p className="version-advisory">已有新数据版本；当前浏览与导出仍固定在原版本。<button type="button" className="text-button" onClick={() => window.location.reload()}>刷新查看新版</button></p>}
      {tab === "status" && <div role="tabpanel" aria-label="更新状态">
        <dl className="version-status"><div><dt>最近任务</dt><dd>{status.label ?? "尚无更新记录"}</dd></div><div><dt>最近成功</dt><dd>{dateLabel(status.lastSuccessAt)}</dd></div><div><dt>任务完成</dt><dd>{dateLabel(status.finishedAt)}</dd></div><div><dt>当前数据生成于</dt><dd>{meta?.generatedAt ?? "—"}</dd></div></dl>
        <p className="feature-note">统计仅包含非历史建制记录；虚拟容器和历史补层另计。四级截止日期：{meta?.levels["4"].asOf}。</p>
        <div className="count-table"><table><thead><tr><th>层级</th><th>建制记录</th><th>虚拟容器</th><th>历史补层</th><th>沿用旧版</th></tr></thead><tbody>{meta && Object.values(meta.levels).map((info) => <tr key={info.label}><th>{info.label}</th><td>{info.count.toLocaleString("zh-CN")}</td><td>{info.virtualCount ?? 0}</td><td>{info.legacyCount ?? 0}</td><td>{info.carriedOverCount ?? 0}</td></tr>)}</tbody></table></div>
        {meta?.asOfStatus === "retained" && <p className="feature-note">截止日期沿用上次已确认版本；抓取时间更新不代表官方截止日期更新。</p>}
        {!!meta && Object.values(meta.levels).some((item) => item.carriedOverCount) && <p className="version-advisory">部分记录因本次抓取失败沿用旧版，搜索与导出中保留原截止日期及标记。</p>}
      </div>}
      {tab === "changes" && <div role="tabpanel" aria-label="版本变化">
        <p className="feature-message">{summary ? summary.initial ? "首次建立比较基线，暂无上一版本" : `与 ${summary.previousGeneratedAt} 比较，共 ${summary.totalEvents.toLocaleString("zh-CN")} 个变更事件` : "当前版本尚无比较报告"}</p>
        <div className="change-filters"><label>变更类型<select aria-label="变更类型" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>{Object.entries(KINDS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>变更层级<select aria-label="变更层级" value={level} onChange={(e) => setLevel(Number(e.target.value))}>{LEVELS.map((item, i) => <option key={item.key} value={i + 1}>{item.label}</option>)}</select></label><span className="feature-note">{summary?.counts[kind][String(level)] ?? 0} 项</span></div>
        <p className="feature-note">记录差异用于核对数据，不代替行政区划批复；虚拟容器不作为建制变更计入。</p>
        {busy ? <p role="status" className="feature-message">加载变更明细…</p> : <ul className="change-list">{changes.slice(page * 20, (page + 1) * 20).map((row) => <li key={row.code}><div><strong>{row.before?.name ?? "—"} → {row.after?.name ?? "—"}</strong><span>{row.code} · 上级：{row.before?.parentCode || "—"} → {row.after?.parentCode || "—"}</span></div>{row.after && /^\d{12}$/.test(row.code) && <button type="button" className="text-button" onClick={() => onLocate(row.code)}>定位当前记录</button>}</li>)}</ul>}
        {changes.length > 20 && <div className="result-pagination"><span>第 {page + 1} / {Math.ceil(changes.length / 20)} 页</span><button type="button" className="secondary-button" disabled={!page} onClick={() => setPage((n) => n - 1)}>上一页</button><button type="button" className="secondary-button" disabled={(page + 1) * 20 >= changes.length} onClick={() => setPage((n) => n + 1)}>下一页</button></div>}
      </div>}
      {tab === "patches" && <div role="tabpanel" aria-label="人工修订"><p className="feature-message">已审核修订 {patches.length} 条。原始 2023 冻结基线保持不变。</p><ul className="change-list">{patches.map((record) => <li key={record.id}><div><strong>{record.code} · {record.name || "上级关系调整"}</strong><span>{record.applied ? "已生效" : "待生效"} · {record.effectiveDate} · {record.reason}</span><a href={record.source} target="_blank" rel="noopener noreferrer">查看公开依据</a></div></li>)}</ul></div>}
    </div>}
  </details>;
}
