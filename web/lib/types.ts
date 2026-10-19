export interface Division {
  code: string;
  name: string;
  level: number;
  type?: string;
  legacy?: boolean;
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

export interface Village {
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
}

/** 搜索索引条目：[名称, 代码, 层级] —— 紧凑数组以压体积 */
export type SearchEntry = [string, string, number];

export interface Meta {
  generatedAt: string;
  levels: Record<string, LevelMeta>;
  legacy: { streets: number; areas: number; note: string };
  search?: { shards: number; entries: number };
  sources: Array<{ name: string; url: string; levels: string; asOf: string }>;
}

/** 分片键：代码前 4 位 */
export function shard(code: string): string {
  return code.slice(0, 4);
}
