import { LEVELS } from "./selection";
import { dataUrl, dataVersion, loadMeta } from "./data";
import type { IndexRow, RegionNode, SearchManifest, SelectionCodes } from "./types";

export interface Catalog { rows: RegionNode[]; byKey: Map<string, RegionNode> }
export interface SearchHit { node: RegionNode; path: RegionNode[] }
const catalogs = new Map<string, Catalog>();
const villageCache = new Map<string, RegionNode[]>();

export async function fetchJson<T>(path: string, signal?: AbortSignal, version?: string): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timeout = setTimeout(abort, 30000);
  try {
    const response = await fetch(dataUrl(path, version), { signal: controller.signal });
    if (response.status === 404 && version && /^[a-f0-9]{24}$/.test(version)) throw new Error("该数据版本已过期或文件缺失，请刷新页面后重试");
    if (!response.ok) throw new Error(`数据加载失败（HTTP ${response.status}），请重试`);
    return await response.json() as T;
  } catch (error) {
    if (controller.signal.aborted && !signal?.aborted) throw new Error("数据加载超时，请重试或缩小搜索范围", { cause: error });
    throw error;
  } finally { clearTimeout(timeout); signal?.removeEventListener("abort", abort); }
}

export function createCatalog(rows: IndexRow[]): Catalog {
  const nodes: RegionNode[] = rows.map(([code, name, level, parentCode, legacy, virtual, extra]) => ({ code, name, level, parentCode, legacy: !!legacy, virtual: !!virtual, ...extra }));
  const byKey = new Map(nodes.map((node) => [`${node.level}:${node.code}`, node]));
  if (byKey.size !== nodes.length) throw new Error("搜索数据包含重复代码，请刷新数据后重试");
  return { rows: nodes, byKey };
}
export function catalogPath(catalog: Catalog, node: RegionNode): RegionNode[] {
  const path = [node]; let child = node;
  while (child.level > 1) {
    const parent = catalog.byKey.get(`${child.level - 1}:${child.parentCode}`);
    if (!parent) throw new Error("区划父层缺失，无法定位该记录");
    path.unshift(parent); child = parent;
  }
  return path;
}
export function pathCodes(path: RegionNode[]): SelectionCodes {
  return Object.fromEntries(path.map((node) => [LEVELS[node.level - 1].key, node.code]));
}
async function manifestAt(version: string, signal?: AbortSignal) {
  const manifest = await fetchJson<SearchManifest>("search/manifest.json", signal, version);
  if (![1, 2].includes(manifest.version) || !Array.isArray(manifest.provinces)) throw new Error("搜索索引不受支持，请刷新页面");
  if (dataVersion(manifest) !== version) throw new Error("数据版本不一致，请刷新页面后重试");
  return manifest;
}
export async function loadManifest(signal?: AbortSignal) { return manifestAt(dataVersion(await loadMeta()), signal); }

function remember(key: string, catalog: Catalog) {
  catalogs.delete(key); catalogs.set(key, catalog);
  while (catalogs.size > 4) catalogs.delete(catalogs.keys().next().value!);
  return catalog;
}
async function upperCatalog(province: string, version: string, format: number, signal?: AbortSignal) {
  if (!/^(?:\d{12}|资料暂缺)$/.test(province)) throw new Error("省级代码无效");
  const key = `${version}:upper:${province}`;
  const cached = catalogs.get(key);
  if (cached) return remember(key, cached);
  const rows = await fetchJson<IndexRow[]>(format === 2 ? `search/upper/${encodeURIComponent(province)}.json` : `search/${encodeURIComponent(province)}.json`, signal, version);
  return remember(key, createCatalog(rows));
}
async function villages(shard: string, version: string, signal?: AbortSignal) {
  if (!/^\d{4}$/.test(shard)) throw new Error("村级分片代码无效");
  const key = `${version}:${shard}`;
  const cached = villageCache.get(key);
  if (cached) { villageCache.delete(key); villageCache.set(key, cached); return cached; }
  const rows = createCatalog(await fetchJson<IndexRow[]>(`search/villages/${shard}.json`, signal, version)).rows;
  villageCache.set(key, rows);
  while (villageCache.size > 8) villageCache.delete(villageCache.keys().next().value!);
  return rows;
}
export async function loadCatalog(province: string, version: string, signal?: AbortSignal): Promise<Catalog> {
  const manifest = await manifestAt(version, signal);
  const info = manifest.provinces.find((p) => p.code === province);
  if (!info) throw new Error("未找到该省份");
  const upper = await upperCatalog(province, version, manifest.version, signal);
  if (manifest.version === 1) return upper;
  const rows = [...upper.rows];
  for (const shard of info.shards ?? []) rows.push(...await villages(shard, version, signal));
  return { rows, byKey: new Map(rows.map((node) => [`${node.level}:${node.code}`, node])) };
}
export async function lookupCode(province: string, code: string, version: string, signal?: AbortSignal) {
  const manifest = await manifestAt(version, signal);
  const info = manifest.provinces.find((p) => p.code === province);
  if (!info) throw new Error("未找到该省份");
  const catalog = await upperCatalog(province, version, manifest.version, signal);
  let node = catalog.rows.find((entry) => entry.code === code);
  if (!node && manifest.version === 2 && info.shards?.includes(code.slice(0, 4))) node = (await villages(code.slice(0, 4), version, signal)).find((entry) => entry.code === code);
  return { catalog, node };
}
function compare(a: SearchHit, b: SearchHit) { return a.node.code < b.node.code ? -1 : a.node.code > b.node.code ? 1 : a.node.level - b.node.level; }
export async function searchRegions(query: string, province: string, level: number, showLegacy: boolean,
  signal: AbortSignal, onProgress: (done: number, total: number) => void) {
  const manifest = await loadManifest(signal); const version = dataVersion(manifest);
  const q = query.trim().toLocaleLowerCase(); const numeric = /^\d+$/.test(q);
  const targets = manifest.provinces.filter((p) => (!province || p.code === province) && (!numeric || p.code.startsWith(q.slice(0, 2))));
  if (province && !manifest.provinces.some((p) => p.code === province)) throw new Error("未找到所选省份");
  const hits: SearchHit[] = []; let total = 0, done = 0, next = 0;
  function offer(hit: SearchHit) {
    let lo = 0, hi = hits.length;
    while (lo < hi) { const mid = (lo + hi) >>> 1; if (compare(hits[mid], hit) < 0) lo = mid + 1; else hi = mid; }
    if (lo < 200) { hits.splice(lo, 0, hit); if (hits.length > 200) hits.pop(); }
  }
  async function scan(rows: RegionNode[], catalog: Catalog) {
    for (let i = 0; i < rows.length; i++) {
      if (i % 2048 === 0) { await new Promise((resolve) => setTimeout(resolve, 0)); if (signal.aborted) throw new DOMException("搜索已取消", "AbortError"); }
      const node = rows[i];
      if ((level && node.level !== level) || !(numeric ? node.code.startsWith(q) : node.name.toLocaleLowerCase().includes(q))) continue;
      const path = catalogPath(catalog, node);
      if (!showLegacy && path.some((parent) => parent.legacy)) continue;
      total++; offer({ node, path });
    }
  }
  async function worker() {
    while (next < targets.length) {
      const p = targets[next++];
      if (signal.aborted) throw new DOMException("搜索已取消", "AbortError");
      const catalog = await upperCatalog(p.code, version, manifest.version, signal);
      await scan(catalog.rows, catalog);
      if (manifest.version === 2 && (!level || level === 5)) {
        for (const shard of p.shards ?? []) {
          if (numeric && q.length >= 4 && !shard.startsWith(q.slice(0, 4))) continue;
          await scan(await villages(shard, version, signal), catalog);
        }
      }
      onProgress(++done, targets.length);
    }
  }
  await Promise.all(Array.from({ length: Math.min(3, targets.length) }, worker));
  return { hits, total, generatedAt: manifest.generatedAt, datasetVersion: manifest.datasetVersion };
}
