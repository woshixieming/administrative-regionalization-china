const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('../web/node_modules/typescript');

// 使用项目已有 TypeScript 编译器运行纯逻辑，不引入测试依赖。
function modules(fetch) {
  const cache = new Map();
  function load(name) {
    const filename = path.resolve(__dirname, '../web/lib', name + '.ts');
    if (cache.has(filename)) return cache.get(filename).exports;
    const module = { exports: {} };
    cache.set(filename, module);
    const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    }).outputText;
    const context = vm.createContext({ module, exports: module.exports, fetch, URL, URLSearchParams, AbortController,
      DOMException, setTimeout, clearTimeout, console,
      require: (dependency) => load(dependency.replace('./', '')) });
    vm.runInContext(source, context, { filename });
    return module.exports;
  }
  return load;
}

// 模拟后端响应，前端测试只验证请求契约和返回值处理；SQL 行为由后端集成测试覆盖。
function apiResponse(url, rows, meta) {
  const parsed = new URL(url, 'http://localhost');
  if (!['/api/search', '/api/export'].includes(parsed.pathname)) return null;
  const params = parsed.searchParams;
  const catalog = new Map(rows.map(([code,name,level,parentCode,legacy,virtual,extra]) =>
    [`${level}:${code}`, {code,name,level,parentCode,legacy:!!legacy,virtual:!!virtual,...extra}]));
  const pathOf = (node) => {
    const path = [node];
    while(node.level > 1) { node = catalog.get(`${node.level-1}:${node.parentCode}`); path.unshift(node); }
    return path;
  };
  const visible = [...catalog.values()].filter((node) => params.get('history') !== '0' || !pathOf(node).some((p)=>p.legacy));
  let body;
  if(parsed.pathname === '/api/search') {
    const q = params.get('q').trim().toLowerCase();
    const hits = visible.filter((n)=>(!params.get('province') || pathOf(n)[0].code===params.get('province')) &&
      (!Number(params.get('level')) || n.level===Number(params.get('level'))) &&
      (/^\d+$/.test(q) ? n.code.startsWith(q) : n.name.toLowerCase().includes(q)))
      .sort((a,b)=>a.code.localeCompare(b.code)||a.level-b.level);
    body = {hits:hits.slice(0,200).map((node)=>({node,path:pathOf(node)})),total:hits.length,
      datasetVersion:params.get('version'),generatedAt:meta.generatedAt};
  } else {
    const level=Number(params.get('level')), code=params.get('code');
    const anchor=catalog.get(`${level}:${code}`);
    const ancestors=new Set(pathOf(anchor).map((n)=>`${n.level}:${n.code}`));
    const keys=new Set(visible.filter((n)=>ancestors.has(`${n.level}:${n.code}`)||pathOf(n)[level-1]?.code===code).map((n)=>`${n.level}:${n.code}`));
    body={datasetVersion:params.get('version'),rows:rows.filter((r)=>keys.has(`${r[2]}:${r[0]}`))};
  }
  return {ok:true,status:200,json:async()=>body};
}

const generatedAt = '2026-10-16 14:04:04';
function fixture() {
  const rows = [
    ['410000000000', '河南省', 1, '', 0, 0],
    ['410100000000', '郑州市', 2, '410000000000', 0, 0],
    ['410000000000', '省直辖县级行政区划', 2, '410000000000', 0, 1],
    ['410102000000', '中原区', 3, '410100000000', 0, 0],
    ['419001000000', '济源市', 3, '410000000000', 0, 0],
    ['419002000000', '历史县', 3, '410000000000', 1, 0],
    ['410102001000', '林山寨街道', 4, '410102000000', 0, 0],
    ['419002001000', '历史街道', 4, '419002000000', 1, 0],
    ['410102001001', '互助社区', 5, '410102001000', 0, 0],
    ['419002001001', '历史社区', 5, '419002001000', 0, 0],
  ];
  const levels = Object.fromEntries([1, 2, 3, 4, 5].map((n) => [String(n), {
    label: 'L' + n, count: 1, source: n === 5 ? '国家统计局' : '民政部',
    asOf: n === 5 ? '2023-06-30' : '2025-12-31', live: n < 5,
  }]));
  const meta = { generatedAt, levels, legacy: { streets: 1, areas: 1 }, sources: [],
    directCities: [{ code: '410000000000', name: '省直辖县级行政区划', level: 2, provinceCode: '410000000000', virtual: true }] };
  const nodes = rows.map(([code, name, level, parent, legacy, virtual]) => ({ code, name, level,
    legacy: !!legacy, virtual: !!virtual,
    ...(level > 1 ? { [['', '', 'provinceCode', 'cityCode', 'areaCode', 'streetCode'][level]]: parent } : {}) }));
  const files = {
    'meta.json': meta,
    'provinces.json': nodes.filter((n) => n.level === 1),
    'l2/410000000000.json': nodes.filter((n) => n.level === 2 && !n.virtual),
    'l3/410100000000.json': nodes.filter((n) => n.level === 3 && n.cityCode === '410100000000'),
    'l3/410000000000.json': nodes.filter((n) => n.level === 3 && n.cityCode === '410000000000'),
    'search/manifest.json': { version: 1, generatedAt, provinces: [{ code: '410000000000', name: '河南省', count: rows.length }], total: rows.length },
    'search/410000000000.json': rows,
  };
  for (const level of [4, 5]) {
    const parentKey = level === 4 ? 'areaCode' : 'streetCode';
    for (const node of nodes.filter((n) => n.level === level)) {
      const file = `l${level}/${node[parentKey].slice(0, 4)}.json`;
      files[file] ??= {};
      (files[file][node[parentKey]] ??= []).push(node);
    }
  }
  const fetch = async (url, options) => {
    if (options?.signal?.aborted) throw new DOMException('aborted', 'AbortError');
    const api = apiResponse(url, rows, meta);
    if (api) return api;
    const key = decodeURIComponent(String(url).split('?')[0].replace('/api/data/', ''));
    const data = files[key];
    return { ok: data !== undefined, status: data === undefined ? 404 : 200, json: async () => data };
  };
  return { rows, meta, nodes, files, load: modules(fetch) };
}

test('同码虚拟地级与省级保持独立，省直辖县有完整路径', () => {
  const { rows, load } = fixture();
  const { createCatalog, catalogPath } = load('catalog');
  const catalog = createCatalog(rows);
  const direct = catalog.byKey.get('3:419001000000');
  assert.equal(catalogPath(catalog, direct).map((n) => n.name).join('/'), '河南省/省直辖县级行政区划/济源市');
  assert.equal(catalog.byKey.size, rows.length);
});

test('直管市同码县级容器可回溯到独立的地级', () => {
  const { load } = fixture();
  const { createCatalog, catalogPath } = load('catalog');
  const rows = [['440000000000','广东省',1,'',0,0], ['441900000000','东莞市',2,'440000000000',0,0],
    ['441900000000','东莞市',3,'441900000000',0,1]];
  const catalog = createCatalog(rows);
  assert.equal(catalogPath(catalog, catalog.byKey.get('3:441900000000')).length, 3);
});

test('链接保存每级代码和历史开关，保留其他参数与片段', () => {
  const { load } = fixture();
  const { selectionUrl, readLocation } = load('selection');
  const url = selectionUrl('http://localhost/?other=yes#browse', { province:'410000000000',city:'410000000000',area:'419001000000' }, false);
  const state = readLocation(new URL(url));
  assert.equal(state.codes.area, '419001000000');
  assert.equal(state.codes.city, state.codes.province);
  assert.equal(state.showLegacy, false);
  assert.equal(new URL(url).searchParams.get('other'), 'yes');
  assert.equal(new URL(url).hash, '#browse');
  assert.throws(() => readLocation(new URL('http://localhost/?area=419001000000')), /无效/);
  assert.throws(() => readLocation(new URL('http://localhost/?province=..%2F..')), /无效/);
});

test('恢复选择验证实际父子关系，不能用合法代码拼出错误路径', async () => {
  const { load, nodes } = fixture();
  const { resolveSelection } = load('selection');
  const provinces = nodes.filter((n) => n.level === 1);
  const direct = await resolveSelection({ province:'410000000000',city:'410000000000',area:'419001000000' }, provinces, true);
  assert.equal(direct.selection.area.name, '济源市');
  await assert.rejects(resolveSelection({ province:'410000000000',city:'410100000000',area:'419001000000' }, provinces, true), /未找到/);
  await assert.rejects(resolveSelection({ province:'410000000000',city:'410000000000',area:'419002000000' }, provinces, false), /历史补层/);
});

test('全局搜索可按代码或名称检索，并隐藏历史父层的村级后代', async () => {
  const { load } = fixture();
  const { searchRegions } = load('catalog');
  const search = (query, history, level=0) => searchRegions(query, '', level, history, new AbortController().signal, () => {});
  assert.equal((await search('419001', true)).hits[0].node.name, '济源市');
  assert.equal((await search('社区', true, 5)).total, 2);
  const result = await search('社区', false, 5);
  assert.equal(result.total, 1);
  assert.equal(result.hits[0].node.name, '互助社区');
});

test('搜索结果上限明确保留真实匹配总数', async () => {
  const { load, rows } = fixture();
  for (let i=2; i<207; i++) rows.push([`410102001${String(i).padStart(3,'0')}`,'测试社区',5,'410102001000',0,0]);
  const result = await load('catalog').searchRegions('社区', '', 5, true, new AbortController().signal, () => {});
  assert.equal(result.total, 207);
  assert.equal(result.hits.length, 200);
});

test('搜索请求取消和 HTTP 失败不会伪装成空结果', async () => {
  const { load } = fixture();
  const controller = new AbortController(); controller.abort();
  await assert.rejects(load('catalog').searchRegions('社区', '', 0, true, controller.signal, () => {}), /aborted/);
  const broken = modules(async () => ({ ok:false,status:500 }));
  await assert.rejects(broken('catalog').loadManifest(), /HTTP 500/);
});

test('区域导出只包含自身和后代，且标明村级来源和截止日期', async () => {
  const { load, rows, meta } = fixture();
  const catalog = load('catalog').createCatalog(rows);
  const path = load('catalog').catalogPath(catalog, catalog.byKey.get('3:410102000000'));
  const data = await load('export').collectExport(path, meta, true, new AbortController().signal);
  assert.equal(data.count, 3);
  assert.equal(data.rows[2].source, '国家统计局');
  assert.equal(data.rows[2].asOf, '2023-06-30');
  assert.equal(data.rows[2].provinceCode, '410000000000');
  assert.ok(data.rows.every((row) => row.areaCode === '410102000000'));
});

test('省级导出遵循历史开关，地址排除虚拟层级名称', async () => {
  const { load, rows, meta } = fixture();
  const catalog = load('catalog').createCatalog(rows);
  const scope = [catalog.byKey.get('1:410000000000')];
  const all = await load('export').collectExport(scope, meta, true, new AbortController().signal);
  const current = await load('export').collectExport(scope, meta, false, new AbortController().signal);
  assert.equal(all.count-current.count, 3);
  const direct = all.rows.find((r) => r.code==='419001000000');
  assert.equal(direct.address, '河南省 / 济源市');
  const historical = all.rows.find((r) => r.code==='419002000000');
  assert.equal(historical.asOf, '2023-06-30');
  assert.equal(historical.status, '历史补层');
});

test('CSV 包含 BOM、双引号转义和公式防护', () => {
  const { load } = fixture();
  const csv = load('export').toCsv([{code:'410102001001',name:'=cmd,"x"\nnext',source:'统计局',asOf:'2023-06-30'}]);
  assert.ok(csv.startsWith('\uFEFF'));
  assert.ok(csv.includes('"\'=cmd,""x""\nnext"'));
  assert.ok(csv.includes('\r\n'));
  assert.ok(csv.includes('"410102001001"'));
});

test('拒绝把新版索引与旧版来源元数据混合导出', async () => {
  const { load, rows, meta } = fixture();
  const catalog = load('catalog').createCatalog(rows);
  const scope = [catalog.byKey.get('1:410000000000')];
  await assert.rejects(load('export').collectExport(scope, { ...meta, generatedAt:'old' }, true, new AbortController().signal), /版本已更新/);
});

function versionedFixture() {
  const base = fixture();
  const version = 'a'.repeat(24);
  const meta = { ...base.meta, datasetVersion: version };
  const files = { 'meta.json': meta };
  for (const [name, value] of Object.entries(base.files)) files[`v/${version}/${name}`] = value;
  files[`v/${version}/search/manifest.json`] = { version: 2, generatedAt, datasetVersion: version,
    provinces: [{code:'410000000000',name:'河南省',count:base.rows.length,upperCount:8,shards:['4101','4190']}], total:base.rows.length };
  files[`v/${version}/search/upper/410000000000.json`] = base.rows.filter((r)=>r[2]<5);
  for (const shard of ['4101','4190']) files[`v/${version}/search/villages/${shard}.json`] = base.rows.filter((r)=>r[2]===5&&r[0].startsWith(shard));
  const requests = [];
  const fetch = async (url,options) => {
    if(options?.signal?.aborted) throw new DOMException('aborted','AbortError');
    const api = apiResponse(url, base.rows, meta);
    if (api) return api;
    const key=decodeURIComponent(String(url).split('?')[0].replace('/api/data/',''));
    requests.push(key);
    return {ok:files[key]!==undefined,status:files[key]===undefined?404:200,json:async()=>files[key]};
  };
  return { ...base, files, requests, version, meta, load:modules(fetch) };
}

test('浏览和导出绑定原版本，发布新版后不会混用分片', async () => {
  const {files,requests,load,version,meta}=versionedFixture();
  const data=load('data');
  const provinces=await data.loadProvinces();
  files['meta.json']={...meta,datasetVersion:'b'.repeat(24)};
  const selected=await load('selection').resolveSelection({province:'410000000000',city:'410100000000',area:'410102000000'},provinces,true);
  assert.equal(selected.selection.area.name,'中原区');
  assert.ok(requests.filter((p)=>p!=='meta.json').every((p)=>p.startsWith(`v/${version}/`)));
  const path=load('selection').selectionPath(selected.selection);
  const exported=await load('export').collectExport(path,meta,true,new AbortController().signal);
  assert.equal(exported.datasetVersion,version);
});

test('固定版本丢失时提示刷新，不能把缺失文件当作无下级', async () => {
  const {files,load,version}=versionedFixture();
  delete files[`v/${version}/l3/410100000000.json`];
  await assert.rejects(load('data').loadAreas('410100000000'),/版本已过期|分片缺失/);
});

test('上四级名称搜索不下载村级索引，代码定位只读目标分片', async () => {
  const {load,requests,version}=versionedFixture();
  const result=await load('catalog').searchRegions('中原','',3,true,new AbortController().signal,()=>{});
  assert.equal(result.total,1);
  assert.ok(!requests.some((p)=>p.includes('search/villages/')));
  const found=await load('catalog').lookupCode('410000000000','410102001001',version);
  assert.equal(found.node.name,'互助社区');
  assert.equal(requests.filter((p)=>p.includes('search/villages/')).join(','),`v/${version}/search/villages/4101.json`);
});

test('搜索通过后端接口提交版本和筛选，不下载全国搜索分片', async () => {
  const base=fixture(), requests=[];
  const load=modules(async (url, options)=> {
    if(String(url).endsWith('meta.json')) return {ok:true,json:async()=>base.meta};
    requests.push(new URL(url,'http://localhost'));
    return apiResponse(url,base.rows,base.meta);
  });
  const result=await load('catalog').searchRegions('社区','410000000000',5,false,new AbortController().signal,()=>{});
  assert.equal(result.total,1);
  assert.equal(requests.length,1);
  assert.equal(requests[0].pathname,'/api/search');
  assert.equal(requests[0].searchParams.get('history'),'0');
  assert.equal(requests[0].searchParams.get('version'),generatedAt);
  assert.equal(requests[0].searchParams.get('province'),'410000000000');
});

test('沿用旧版和人工修订来源进入导出，不冒充当前官方版本', () => {
  const {load,meta}=fixture();
  const path=[{code:'110105020000',name:'街道',level:4,parentCode:'110105000000',carriedOver:true,carriedAsOf:'2024-12-31'}];
  const row=load('export').exportRow(path,meta);
  assert.equal(row.asOf,'2024-12-31');
  assert.equal(row.carriedOver,true);
  assert.match(row.status,/沿用旧版/);
  const patched=load('selection').provenance({code:'110101001001',name:'新社区',level:5,parentCode:'110101001000',patchId:'approved',patchSource:'https://example.gov.cn/notice',effectiveDate:'2026-01-01'},meta);
  assert.equal(patched.status,'人工修订');
  assert.equal(patched.asOf,'2026-01-01');
});
