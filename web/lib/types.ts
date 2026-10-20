export interface RecordSource {
  carriedOver?: boolean;
  carriedAsOf?: string;
  patchId?: string;
  patchSource?: string;
  effectiveDate?: string;
}
export interface Division extends RecordSource {
  code: string;
  name: string;
  level: number;
  type?: string;
  legacy?: boolean;
  virtual?: boolean;
}

export interface City extends Division {
  provinceCode: string;
}

export interface Area extends Division {
  cityCode: string;
}

export interface Street extends Division {
  areaCode: string;
}

export interface Village extends RecordSource {
  code: string;
  name: string;
  streetCode: string;
}

export interface LevelMeta {
  label: string;
  source: string;
  asOf: string;
  count: number;
  live: boolean;
  displayCount?: number;
  virtualCount?: number;
  legacyCount?: number;
  carriedOverCount?: number;
}

export interface Meta {
  generatedAt: string;
  levels: Record<string, LevelMeta>;
  legacy: { streets: number; areas: number; note: string };
  sources: Array<{ name: string; url: string; levels: string; asOf: string }>;
  directCities?: City[];
  searchVersion?: number;
  datasetVersion?: string;
  asOfStatus?: string;
}

export type LevelKey = "province" | "city" | "area" | "street" | "village";
export type Selection = Partial<Record<LevelKey, Division | Village>>;
export type SelectionCodes = Partial<Record<LevelKey, string>>;
export type RegionNode = Division & { parentCode: string };
export type IndexRow = [string, string, number, string, number, number, RecordSource?];
export interface SearchManifest {
  version: number;
  generatedAt: string;
  datasetVersion?: string;
  provinces: Array<{ code: string; name: string; count: number; upperCount?: number; shards?: string[] }>;
  total: number;
}

/** 分片键：代码前 4 位 */
export function shard(code: string): string {
  return code.slice(0, 4);
}
