import { shard, type Area, type City, type Division, type Meta, type Street, type Village } from "./types";

let metadata: Promise<Meta> | undefined;
const cache = new Map<string, Promise<unknown>>();

export function dataVersion(meta: { datasetVersion?: string; generatedAt: string }): string {
  return meta.datasetVersion ?? meta.generatedAt;
}

export function dataUrl(path: string, version?: string): string {
  return `/api/data/${version && /^[a-f0-9]{24}$/.test(version) ? `v/${version}/` : ""}${path}`;
}

async function get<T>(path: string, fallback: T): Promise<T> {
  const meta = await loadMeta();
  const key = dataUrl(path, dataVersion(meta));
  let promise = cache.get(key) as Promise<T> | undefined;
  if (promise) { cache.delete(key); cache.set(key, promise); }
  else {
    promise = fetch(key).then((response) => {
      if (response.status === 404 && !meta.datasetVersion) return fallback;
      if (response.status === 404) throw new Error("当前数据版本已过期或分片缺失，请刷新页面后重试");
      if (!response.ok) throw new Error(`数据加载失败（HTTP ${response.status}）`);
      return response.json() as Promise<T>;
    }).catch((error) => { cache.delete(key); throw error; });
    cache.set(key, promise);
    while (cache.size > 32) cache.delete(cache.keys().next().value!);
  }
  return promise;
}

export function loadMeta(): Promise<Meta> {
  if (!metadata) metadata = fetch("/api/data/meta.json", { cache: "no-store" }).then(async (response) => {
    if (!response.ok) throw new Error(`数据版本信息加载失败（HTTP ${response.status}）`);
    const meta = await response.json() as Meta;
    if (!meta.generatedAt || [1, 2, 3, 4, 5].some((level) => !meta.levels[String(level)])) throw new Error("数据版本信息不完整，请刷新站点数据后重试");
    if (meta.datasetVersion && !/^[a-f0-9]{24}$/.test(meta.datasetVersion)) throw new Error("数据版本号无效");
    return meta;
  }).catch((error) => { metadata = undefined; throw error; });
  return metadata;
}

export function loadProvinces() { return get<Division[]>("provinces.json", []); }
export async function loadCities(provinceCode: string) {
  const [cities, meta] = await Promise.all([get<City[]>(`l2/${provinceCode}.json`, []), loadMeta()]);
  return [...cities, ...(meta.directCities ?? []).filter((city) => city.provinceCode === provinceCode)];
}
export function loadAreas(cityCode: string) { return get<Area[]>(`l3/${cityCode}.json`, []); }
export async function loadStreets(areaCode: string): Promise<Street[]> {
  const groups = await get<Record<string, Street[]>>(`l4/${shard(areaCode)}.json`, {});
  return groups[areaCode] ?? [];
}
export async function loadVillages(streetCode: string): Promise<Village[]> {
  const groups = await get<Record<string, Village[]>>(`l5/${shard(streetCode)}.json`, {});
  return groups[streetCode] ?? [];
}
