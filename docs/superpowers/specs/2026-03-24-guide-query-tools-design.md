# CodeKB 高效查询工具设计文档

## 1. 背景与问题

### 当前痛点

1. **MCP 调用次数过多**：查询一个组件的用法需要依次调用 `get_structure` → `search_code` → `get_file_content` → `get_usage_examples`，消耗大量上下文
2. **搜索结果不精准**：向量语义搜索对精确符号查询（如 `ColumnFreezingList`）返回大量无关结果，找不到实际调用代码
3. **缺少一键查询工具**：没有"输入组件名直接返回用法说明"的工具，需要 agent 多步拼凑

### 解决方向

- 新增 4 个场景化 MCP 工具，每个工具内部编排多个子查询，一次调用返回完整结果
- **SQLite 精确查询 + 图遍历**作为主要搜索方式（3/4 工具不需要向量搜索）
- 仅 `recommend_component` 在缓存未命中时使用 LLM 处理自然语言，其余工具零 LLM
- 混合模式：缓存命中直接返回，未命中时 LLM 生成 + 缓存

### 设计原则

- **零维护成本**：不维护同义词表、不依赖 jieba 分词、不加 FTS5 索引、不加新的 tokenizer
- **语言无关**：4 个工具基于 tree-sitter 提取的通用符号结构，支持所有 7 种已接入语言（ArkTS、TypeScript、JavaScript、Python、Java、Kotlin、Swift）
- **复用现有基础设施**：SQLite symbols/calls/imports 表 + 现有 LLM 配置

## 2. 搜索架构

### 核心思路：精确查询为主，LLM 兜底自然语言

```
查询请求
  ↓
┌─ 已知符号名？──→ SQLite 精确查 symbols/calls/imports    （毫秒级，零 LLM）
│                 get_component_guide / get_usage_examples / get_api_guide
│
└─ 自然语言描述？─→ guide_cache 命中？──→ 直接返回       （毫秒级，零 LLM）
                   recommend_component   ↓ 未命中
                                        LLM 分析需求 + 候选符号 → 生成推荐 → 缓存
```

### 4 个工具的搜索方式

| 工具 | 搜索方式 | 是否需要 LLM | 是否需要向量搜索 |
|------|----------|-------------|----------------|
| `get_component_guide` | SQLite 精确查 name + calls + imports | 仅生成 description/example 时 | 否 |
| `get_usage_examples` | SQLite 精确查 imports + calls + source LIKE | 仅生成 context 摘要时 | 否 |
| `recommend_component` | 缓存命中→返回；未命中→LLM 理解需求 | 缓存未命中时 | 否 |
| `get_api_guide` | SQLite 精确查 module + imports + file_tree | 仅生成 guide 时 | 否 |

### 图遍历说明

利用 SQLite 已有表通过 SQL 模拟图遍历，无需额外存储：

- `symbols` 表 → 节点
- `calls` 表 → 边（关系类型：calls）
- `imports` 表 → 边（关系类型：imports）

## 3. 新增 4 个 MCP 工具

### 3.1 `get_component_guide` — 组件使用指南

**语言无关**：适用于 ArkTS struct、Kotlin class、Swift class、TypeScript function、Python class 等所有已支持语言的组件。

**输入：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| repo_name | string | 是 | 仓库名 |
| symbol_name | string | 是 | 组件/类名 |
| module | string | 否 | 模块过滤 |

**输出：**

```json
{
  "symbol_name": "ColumnFreezingList",
  "type": "struct",
  "language": "typescript",
  "file": "bizCommon/biz_ui/src/main/ets/view/ColumnFreezingList.ets",
  "module": "biz_ui",
  "description": "列冻结横向滚动列表组件，左侧列固定 + 右侧横向滚动",
  "properties": [
    { "name": "data", "type": "LazyDataSource<object>", "required": true, "description": "数据源" }
  ],
  "methods": [
    { "name": "aboutToAppear", "signature": "aboutToAppear(): void", "description": "初始化滚动同步" }
  ],
  "usage_example": "ColumnFreezingList({ data: this.dataList, ... })",
  "related_symbols": ["ColumnFreezingListV2", "ColumnFreezingList3", "CacheScroll"]
}
```

**内部流程：**

```
1. symbols WHERE name = ? → 获取定义、属性、方法、源码
2. calls WHERE callee_name = ? → 找到调用方文件
3. imports WHERE imported_names LIKE ? → 找到导入该符号的文件
4. symbols WHERE file_path IN (...) AND source LIKE ? → 提取调用代码片段
5. 检查 guide_cache
   ├─ 命中 → 直接返回
   └─ 未命中 → LLM 根据步骤 1-4 的上下文生成 description + usage_example → 缓存
```

### 3.2 `get_usage_examples` — 实际调用示例（优化现有）

**输入：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| repo_name | string | 是 | 仓库名 |
| symbol_name | string | 是 | 符号名 |
| module | string | 否 | 模块过滤 |
| top_k | int | 否 | 最大示例数，默认 5 |

**输出：**

```json
{
  "symbol_name": "ColumnFreezingList",
  "examples": [
    {
      "file": "entry/src/main/ets/pages/StockPage.ets",
      "line_range": "45-80",
      "code": "ColumnFreezingList({ data: this.stocks, ... })",
      "context": "股票列表页面中使用列冻结列表展示行情数据"
    }
  ]
}
```

**内部流程：**

```
1. symbols WHERE name = ? → 获取定义文件路径（用于排除）
2. imports WHERE imported_names LIKE ? → 找到所有导入该符号的文件
3. calls WHERE callee_name = ? → 找到调用关系
4. symbols WHERE file_path IN (调用方文件) AND source LIKE ? → 提取调用代码
5. 过滤掉定义文件本身，只返回调用方代码
6. 检查 guide_cache
   ├─ 命中 → 直接返回
   └─ 未命中 → LLM 生成 context 摘要 → 缓存
```

### 3.3 `recommend_component` — 智能组件推荐

**输入：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| repo_name | string | 是 | 仓库名 |
| requirement | string | 是 | 需求描述（支持中文） |
| module | string | 否 | 模块过滤 |
| top_k | int | 否 | 推荐数量，默认 3 |

**输出：**

```json
{
  "requirement": "带冻结列的表格列表",
  "recommendations": [
    {
      "symbol_name": "ColumnFreezingList",
      "module": "biz_ui",
      "type": "struct",
      "relevance_score": 0.95,
      "reason": "支持左侧列冻结 + 右侧横向滚动，适合表格类数据展示",
      "brief_usage": "ColumnFreezingList({ data, builderFirstColumnHeader, ... })"
    }
  ]
}
```

**内部流程：**

```
1. 检查 guide_cache
   ├─ 命中 → 直接返回缓存的推荐结果
   └─ 未命中 → 继续：
2. 获取 repo 下所有 export 级别符号的列表（name + kind + module + signature）
3. 将 requirement + 符号列表交给 LLM，让 LLM：
   - 理解自然语言需求（中英文均可）
   - 从符号列表中筛选匹配的组件
   - 生成 reason + brief_usage
4. 写入 guide_cache → 返回
```

**LLM prompt 示例：**

```
用户需求：{requirement}

以下是 {repo_name} 仓库中可用的组件/类列表：
1. ColumnFreezingList (struct) - biz_ui 模块
2. TitleBar (struct) - biz_ui 模块
3. StatusComponent (struct) - biz_ui 模块
...

请推荐最匹配用户需求的 {top_k} 个组件，对每个组件给出：
- relevance_score (0-1)
- reason（一句话说明匹配理由）
- brief_usage（一行使用示例）
```

### 3.4 `get_api_guide` — API 接入指南

**输入：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| repo_name | string | 是 | 仓库名 |
| library_name | string | 是 | 库/模块名 |
| module | string | 否 | 模块过滤 |

**输出：**

```json
{
  "library_name": "biz_ui",
  "guide": {
    "description": "通用业务 UI 组件库，提供金融场景常用组件",
    "import_statement": "import { ColumnFreezingList, TitleBar } from '@jfzt/biz_ui'",
    "components": [
      { "name": "ColumnFreezingList", "brief": "列冻结横向滚动列表" },
      { "name": "TitleBar", "brief": "通用标题栏" }
    ],
    "setup": "1. 在 oh-package.json5 中添加依赖\n2. import 组件",
    "example": "完整接入代码示例"
  }
}
```

**内部流程：**

```
1. file_tree WHERE path LIKE '%Index%' OR path LIKE '%index%' → 找导出文件
2. imports WHERE module LIKE '%library_name%' → 找导入该库的文件
3. symbols WHERE repo_module = library_name → 获取模块所有符号
4. get_file_content(导出文件) → 获取 import/export 语句
5. 检查 guide_cache
   ├─ 命中 → 直接返回
   └─ 未命中 → LLM 根据步骤 1-4 的上下文生成 guide → 缓存
```

## 4. 缓存设计

### 4.1 缓存表（在 metadata.db 中新增）

```sql
CREATE TABLE IF NOT EXISTS guide_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_name TEXT NOT NULL,
    tool_type TEXT NOT NULL,        -- 'component_guide' | 'usage_examples' | 'recommend' | 'api_guide'
    query_key TEXT NOT NULL,        -- symbol_name 或 requirement 或 library_name
    module TEXT DEFAULT '',
    result_json TEXT NOT NULL,      -- 完整的 JSON 结果
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(repo_name, tool_type, query_key, module)
);

CREATE INDEX IF NOT EXISTS idx_guide_cache_lookup
    ON guide_cache(repo_name, tool_type, query_key, module);
```

### 4.2 缓存流程

```
查询请求
  ↓
检查 guide_cache(repo_name, tool_type, query_key, module)
  ↓
  ├─ 命中 → 直接返回缓存的 JSON 结果（毫秒级，零 LLM）
  └─ 未命中 → 编排子查询（SQLite 精确查询）
                ↓
              组装上下文 → 调用 LLM 生成结果
                ↓
              写入 guide_cache → 返回
```

### 4.3 LLM 调用策略

| 工具 | LLM 输入 | LLM 输出 | 何时调用 |
|------|----------|----------|----------|
| get_component_guide | 符号结构 + 源码 + 调用示例 | description + usage_example | 缓存未命中 |
| get_usage_examples | 代码片段 | context 摘要 | 缓存未命中 |
| recommend_component | 需求描述 + 符号列表 | relevance_score + reason + brief_usage | 缓存未命中 |
| get_api_guide | 导出列表 + README + 组件结构 | description + setup + example | 缓存未命中 |

### 4.4 缓存失效

仓库重新索引时（`codekb repo index`），清除该仓库的所有 guide_cache：

```python
def clear_guide_cache(self, repo_name: str):
    conn.execute("DELETE FROM guide_cache WHERE repo_name = ?", (repo_name,))
```

### 4.5 LLM 配置

复用现有 `.env` 中的 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 配置。

## 5. 文件结构变更

```
src/codekb/
├── mcp/
│   ├── server.py                # 修改：注册 4 个新工具 + 路由
│   └── guide_cache.py           # 新增：缓存 CRUD
├── retrieval/
│   ├── guide_generator.py       # 新增：4 个工具的核心逻辑
│   ├── structure_query.py       # 不变：复用
│   ├── semantic_search.py       # 不变：保留，现有工具仍可用
│   └── hybrid_search.py         # 不变：保留，现有工具仍可用
├── storage/
│   └── sqlite_store.py          # 修改：新增 guide_cache 表
```

### 各文件职责

| 文件 | 变更类型 | 职责 |
|------|----------|------|
| `mcp/server.py` | 修改 | 注册 4 个新工具，在 `_handle_tool()` 中新增路由分支 |
| `mcp/guide_cache.py` | 新增 | 缓存查询、写入、清除。操作 metadata.db 的 guide_cache 表 |
| `retrieval/guide_generator.py` | 新增 | 4 个工具的核心逻辑：编排 SQLite 查询 → 组装 prompt → 调用 LLM → 解析结果 |
| `storage/sqlite_store.py` | 修改 | 新增 guide_cache 表创建方法 |

**变更量：2 个新文件 + 2 个修改文件。**

## 6. 输出限制

借鉴 CodeGraph 的设计，每个工具的输出有字符上限：

| 工具 | 输出上限 | 说明 |
|------|----------|------|
| get_component_guide | 12000 字符 | 完整指南 + 代码示例 |
| get_usage_examples | 8000 字符 | 最多 5 个示例，每个截断到合理长度 |
| recommend_component | 6000 字符 | 推荐列表 + 简要用法 |
| get_api_guide | 15000 字符 | 完整接入指南 |

超出时截断代码片段，优先保留结构化信息（属性列表、方法签名等）。

## 7. 实施优先级

| 阶段 | 内容 | 依赖 |
|------|------|------|
| P0 | `sqlite_store.py` 新增 guide_cache 表 | 无 |
| P0 | `guide_cache.py` 缓存管理 | P0 |
| P1 | `guide_generator.py` + `get_component_guide` 工具 | P0 |
| P1 | `get_usage_examples` 工具（优化版） | P0 |
| P2 | `recommend_component` 工具 | P0 |
| P2 | `get_api_guide` 工具 | P0 |
| P3 | `server.py` 集成注册 | P1 + P2 |

## 8. 调研参考

### 行业方案对比

| 项目 | 搜索方式 | 自然语言处理 | LLM 依赖 |
|------|----------|-------------|----------|
| CodeGraph (colbymchenry) | FTS5 BM25 + 图遍历 | 无，直接分词前缀匹配 | 零 |
| Zoekt (Sourcegraph) | Trigram 索引 + 正则 | 无 | 零 |
| OpenGrok (Oracle) | Lucene + 语言级分词器 | 无 | 零 |
| srclight | 多 FTS5 索引 + 可选向量 | 无 | 可选 |

### 关键借鉴

1. **CodeGraph**：精确查询走 SQLite，不做自然语言理解，BM25 + 多信号打分
2. **行业共识**：代码搜索结构优于语义，精确符号名用 SQLite，自然语言交给 LLM（而非自己做 NLP）
3. **最终决策**：不在 CodeKB 内部做同义词/分词/FTS5 等基础设施，自然语言需求直接交给 LLM 理解，结果缓存后零成本复用
