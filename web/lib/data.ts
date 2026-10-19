import {
  shard,
  type Area,
  type City,
  type Division,
  type Meta,
  type SearchEntry,
  type Street,
  type Village,
} from "./types";

const BASE = "/data";
const cache = new Map<string, Promise<unknown>>();

async function get<T>(path: string, fallback: T): Promise<T> {
  const key = `${BASE}/${path}`;
  let p = cache.get(key) as Promise<T> | undefined;
  if (!p) {
    p = fetch(key)
      .then((r) => {
        // 分片缺失属于正常情况（如无下级建制），回退为空值而非抛错
        if (!r.ok) return fallback;
        return r.json() as Promise<T>;
      })
      .catch((e) => {
        cache.delete(key);
        throw e;
      });
    cache.set(key, p as Promise<unknown>);
  }
  return p;
}

export function loadMeta() {
  return get<Meta>("meta.json", {
    generatedAt: "",
    levels: {},
    legacy: { streets: 0, areas: 0, note: "" },
    sources: [],
  });
}

export function loadProvinces() {
  return get<Division[]>("provinces.json", []);
}

export function loadCities(provinceCode: string) {
  return get<City[]>(`l2/${provinceCode}.json`, []);
}

export function loadAreas(cityCode: string) {
  return get<Area[]>(`l3/${cityCode}.json`, []);
}

/** 乡级按县级前 4 位分片，取回后筛出该县的条目 */
export async function loadStreets(areaCode: string): Promise<Street[]> {
  const g = await get<Record<string, Street[]>>(`l4/${shard(areaCode)}.json`, {});
  return g[areaCode] ?? [];
}

/** 村级按乡级前 4 位分片，取回后筛出该乡的条目 */
export async function loadVillages(streetCode: string): Promise<Village[]> {
  const g = await get<Record<string, Village[]>>(`l5/${shard(streetCode)}.json`, {});
  return g[streetCode] ?? [];
}

/** 搜索索引：L1–L3 约 3 千条，单文件常驻 */
export function loadSearchTop() {
  return get<SearchEntry[]>("sidx/top.json", []);
}

/** 搜索索引：L4–L5 按名称首字分片，取回该首字下的全部条目 */
export function loadSearchShard(query: string) {
  const ch = query.trim()[0];
  if (!ch) return Promise.resolve<SearchEntry[]>([]);
  return get<SearchEntry[]>(`sidx/u${(ch.codePointAt(0) ?? 0).toString(16)}.json`, []);
}
