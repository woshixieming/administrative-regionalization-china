import { catalogPath, loadCatalog, loadManifest } from "./catalog";
import { LEVELS, provenance } from "./selection";
import { dataVersion } from "./data";
import type { Meta, RegionNode } from "./types";

export function exportRow(path: RegionNode[], meta: Meta) {
  const node = path[path.length - 1];
  const fields: Record<string, string | number | boolean> = {
    code: node.code, name: node.name, level: node.level, parentCode: node.parentCode,
  };
  LEVELS.forEach(({ key }, i) => {
    fields[key] = path[i]?.name ?? "";
    fields[`${key}Code`] = path[i]?.code ?? "";
  });
  return { ...fields, address: path.filter((n) => !n.virtual).map((n) => n.name).join(" / "),
    ...provenance(node, meta), legacy: path.some((n) => n.legacy), virtual: !!node.virtual,
    carriedOver: !!node.carriedOver, carriedAsOf: node.carriedAsOf ?? "", patchId: node.patchId ?? "", effectiveDate: node.effectiveDate ?? "" };
}

export function toCsv(rows: ReturnType<typeof exportRow>[]) {
  if (!rows.length) throw new Error("没有可导出的记录");
  const headers = Object.keys(rows[0]);
  const cell = (value: unknown) => {
    let text = String(value ?? "");
    if (/^\s*[=+@-]/.test(text) || /^[\t\r\n]/.test(text)) text = `'${text}`;
    return `"${text.replaceAll('"', '""')}"`;
  };
  return "\uFEFF" + [headers.map(cell).join(","), ...rows.map((row) => headers.map((key) => cell((row as Record<string, unknown>)[key])).join(","))].join("\r\n");
}

export async function collectExport(path: RegionNode[], meta: Meta, showLegacy: boolean, signal: AbortSignal) {
  if (!path.length) throw new Error("请先选择要导出的区域");
  const manifest = await loadManifest(signal);
  if (dataVersion(manifest) !== dataVersion(meta)) throw new Error("数据版本已更新，请刷新页面后重新导出");
  const province = manifest.provinces.find((p) => p.code === path[0].code);
  if (!province) throw new Error("所选省份不存在于当前版本，请重新选择");
  const catalog = await loadCatalog(province.code, dataVersion(meta), signal);
  const anchor = path[path.length - 1];
  const rows: ReturnType<typeof exportRow>[] = [];
  for (let i = 0; i < catalog.rows.length; i++) {
    if (i % 1000 === 0) await new Promise((resolve) => setTimeout(resolve, 0));
    const node = catalog.rows[i];
    if (signal.aborted) throw new DOMException("导出已取消", "AbortError");
    if (node.level < anchor.level) continue;
    const fullPath = catalogPath(catalog, node);
    if (fullPath[anchor.level - 1].code !== anchor.code || (!showLegacy && fullPath.some((n) => n.legacy))) continue;
    rows.push(exportRow(fullPath, meta));
  }
  if (!rows.length) throw new Error("当前筛选下没有可导出的记录");
  return { datasetVersion: meta.datasetVersion, generatedAt: meta.generatedAt, exportedAt: new Date().toISOString(),
    scope: path.map((node) => ({ code: node.code, name: node.name, level: node.level })),
    showLegacy, count: rows.length, rows };
}

export function downloadFile(content: string, name: string, mime: string) {
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
