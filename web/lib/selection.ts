import { loadAreas, loadCities, loadStreets, loadVillages } from "./data";
import type { Division, LevelKey, Meta, RegionNode, Selection, SelectionCodes, Village } from "./types";

export const LEVELS = [
  { key: "province", label: "省级", hint: "省 / 自治区 / 直辖市" },
  { key: "city", label: "地级", hint: "地级市 / 地区 / 自治州 / 盟" },
  { key: "area", label: "县级", hint: "区 / 县 / 县级市 / 旗" },
  { key: "street", label: "乡级", hint: "街道 / 镇 / 乡 / 苏木" },
  { key: "village", label: "村级", hint: "社区居委会 / 村委会 / 嘎查" },
] as const;

export function selectionPath(selection: Selection): RegionNode[] {
  return LEVELS.flatMap((level, index) => {
    const item = selection[level.key];
    return item ? [{ ...item, level: index + 1, parentCode: index ? selection[LEVELS[index - 1].key]?.code ?? "" : "" }] : [];
  });
}

export function selectionCodes(selection: Selection): SelectionCodes {
  return Object.fromEntries(LEVELS.flatMap(({ key }) => selection[key] ? [[key, selection[key]!.code]] : []));
}

export function readLocation(url: URL): { codes: SelectionCodes; showLegacy: boolean } {
  const codes: SelectionCodes = {};
  let gap = false;
  for (const { key } of LEVELS) {
    const code = url.searchParams.get(key);
    if (code === null) { gap = true; continue; }
    if (gap || (key === "province" ? !/^(?:\d{12}|资料暂缺)$/.test(code) : !/^\d{12}$/.test(code))) {
      throw new Error("分享链接的层级或代码无效，请重新选择行政区划");
    }
    codes[key] = code;
  }
  return { codes, showLegacy: url.searchParams.get("history") !== "0" };
}

export function selectionUrl(base: string, codes: SelectionCodes, showLegacy: boolean): string {
  const url = new URL(base);
  url.searchParams.delete("code");
  for (const { key } of LEVELS) {
    url.searchParams.delete(key);
    if (codes[key]) url.searchParams.set(key, codes[key]!);
  }
  url.searchParams.set("history", showLegacy ? "1" : "0");
  return url.toString();
}

export function provenance(node: RegionNode, meta: Meta) {
  const historical = !!node.legacy;
  const level = meta.levels[historical ? "5" : String(node.level)];
  return {
    source: node.patchId ? node.patchSource ?? "人工修订" : historical ? "国家统计局 2023 快照（历史补层）" : level.source,
    asOf: node.patchId ? node.effectiveDate ?? "未核验" : node.carriedOver ? node.carriedAsOf ?? "未核验" : level.asOf,
    status: node.patchId ? "人工修订" : node.carriedOver ? "沿用旧版（本次抓取失败）" : historical ? "历史补层" : node.virtual ? "显示用层级容器" : node.level === 5 ? "冻结快照" : level.live ? "官方四级数据" : "回退数据",
  };
}

export async function resolveSelection(codes: SelectionCodes, provinces: Division[], showLegacy: boolean) {
  const lists: Record<LevelKey, Array<Division | Village>> = { province: provinces, city: [], area: [], street: [], village: [] };
  const selection: Selection = {};
  const loaders = [loadCities, loadAreas, loadStreets, loadVillages];
  for (let i = 0; i < LEVELS.length; i++) {
    const key = LEVELS[i].key;
    if (!codes[key]) break;
    const item = lists[key].find((entry) => entry.code === codes[key]);
    if (!item || (!showLegacy && "legacy" in item && item.legacy)) {
      throw new Error(`未找到链接中的${LEVELS[i].label}，数据可能已变更，或该记录为历史补层`);
    }
    selection[key] = item;
    if (i < loaders.length) lists[LEVELS[i + 1].key] = await loaders[i](item.code);
  }
  return { lists, selection };
}
