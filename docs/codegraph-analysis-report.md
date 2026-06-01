# CodeGraph 深度分析报告 + CodeKB 借鉴建议

## 1. 项目概述

**CodeGraph** 是一个为 AI 编程 Agent（Claude Code、Cursor、Codex 等）提供**语义代码知识图谱**的 MCP Server 工具。

**核心价值主张**：通过预构建的代码知识图谱（符号关系、调用图、代码结构），让 AI Agent 在回答架构问题时不需要逐个文件 grep/Read，而是直接查询图谱。

**Benchmark 数据**：
- 平均 25% 更便宜、57% 更少 token、23% 更快、62% 更少 tool calls
- 支持 7 种语言、4 个真实项目的 AB 测试验证

**技术栈**：TypeScript + web-tree-sitter（WASM）+ SQLite + MCP Server

**项目规模**：118 个 TS 源文件，支持 30 种编程语言

## 2. 架构分析

### 2.1 整体架构

```
源代码 → Tree-sitter 解析 → 符号提取 → 图存储(SQLite) → MCP Server → AI Agent 查询
            ↓                    ↓              ↓               ↓
      grammar.wasm       LanguageExtractor    nodes/edges     tools/
      30种语言           21种语言策略          FTS5全文搜索    10个工具
```

**核心模块划分**：

| 模块 | 文件数 | 职责 |
|------|--------|------|
| `src/extraction/` | ~25 | Tree-sitter 解析 + 符号提取（含 21 种语言策略） |
| `src/graph/` | 3 | BFS/DFS 图遍历 + 查询管理 |
| `src/db/` | 4 | SQLite 存储层（nodes/edges/files/unresolved_refs） |
| `src/mcp/` | 10 | MCP Server 实现（daemon/engine/session/tools/transport） |
| `src/resolution/` | ~15 | 导入解析（import resolution）、框架识别（17 种框架） |
| `src/sync/` | 5 | 文件监听（chokidar）、Git hooks、增量同步 |
| `src/search/` | 2 | 查询解析（支持 `kind:function path:src` 语法） |

### 2.2 数据流

1. **索引阶段**：`codegraph init -i` → 扫描项目文件 → tree-sitter 解析 → 提取 nodes/edges → 写入 SQLite
2. **同步阶段**：FileWatcher（chokidar）监听文件变更 → 增量 reindex（基于 content_hash）
3. **查询阶段**：Agent 调用 MCP 工具 → ToolHandler → GraphTraverser/QueryBuilder → 返回结果

### 2.3 关键设计模式

**1. LanguageExtractor 协议**（和 CodeKB 的 LanguageStrategy 几乎一样）

```typescript
export interface LanguageExtractor {
  functionTypes: string[];
  classTypes: string[];
  methodTypes: string[];
  // ... 各类节点类型定义
  nameField: string;
  bodyField: string;
  getSignature?: (node, source) => string | undefined;
  getVisibility?: (node) => string | undefined;
  extractImport?: (node, source) => ImportInfo | null;
}
```

每个语言一个实现文件：`java.ts`, `kotlin.ts`, `swift.ts`, `python.ts` 等。**这和 CodeKB 的 LanguageStrategy 是完全相同的模式**。

**2. MCP Daemon 模式**

CodeGraph 不是简单的 stdio MCP Server，而是实现了 **daemon 模式**：
- 长期运行的进程（不是每次调用都启动新进程）
- 通过 Unix socket/pipe 通信
- 支持多项目切换（每个项目独立的 .codegraph/ 索引目录）
- 跨进程锁保证索引一致性

**3. 自适应 Explore Budget**

```typescript
export function getExploreBudget(fileCount: number): number {
  if (fileCount < 500) return 1;
  if (fileCount < 5000) return 2;
  if (fileCount < 15000) return 3;
  if (fileCount < 25000) return 4;
  return 5;
}
```

根据项目规模动态调整探索预算，避免小项目过度探索浪费 token。

## 3. 核心实现

### 3.1 知识图谱 Schema

**Nodes 表**：存储代码符号
```sql
CREATE TABLE nodes (
    id TEXT PRIMARY KEY,         -- hash(file_path + qualified_name)
    kind TEXT NOT NULL,          -- function/class/method/interface/...
    name TEXT NOT NULL,          -- 简单名 "calculateTotal"
    qualified_name TEXT NOT NULL,-- 全限定名 "src/utils.ts::MathHelper.calculateTotal"
    file_path TEXT NOT NULL,
    language TEXT NOT NULL,
    start_line/end_line/start_column/end_column INTEGER,
    docstring TEXT,
    signature TEXT,
    visibility TEXT,             -- public/private/protected
    is_exported/is_async/is_static/is_abstract INTEGER,
    decorators TEXT,             -- JSON array
    type_parameters TEXT         -- JSON array
);
```

**Edges 表**：存储关系
```sql
CREATE TABLE edges (
    source TEXT NOT NULL,        -- 调用者
    target TEXT NOT NULL,        -- 被调用者
    kind TEXT NOT NULL,          -- calls/contains/imports/extends/implements/references/type_of/...
    metadata TEXT,               -- JSON
    line INTEGER,
    col INTEGER,
    provenance TEXT              -- 来源追踪
);
```

**对比 CodeKB**：CodeKB 的 schema 更简单（symbols 表 + calls 表 + imports 表），但缺少 extends/implements/references/type_of 等关系类型。CodeGraph 有 **13 种边类型**。

### 3.2 MCP 工具设计（10 个工具）

| 工具 | 功能 | 输入 |
|------|------|------|
| `codegraph_search` | 符号名搜索 | query |
| `codegraph_context` | **主工具**——给 agent 提供任务上下文 | task 描述 |
| `codegraph_callers` | 查调用者 | symbol |
| `codegraph_callees` | 查被调用者 | symbol |
| `codegraph_impact` | 影响分析（重构前用） | symbol |
| `codegraph_node` | 单个符号详情 + 代码 | symbol, includeCode |
| `codegraph_explore` | 批量探索多个符号（按文件分组） | query (符号名列表) |
| `codegraph_status` | 索引健康检查 | projectPath |
| `codegraph_files` | 列出项目文件 | path, language, kind |
| `codegraph_trace` | 深度调用链追踪 | symbol |

**关键设计**：
- `codegraph_context` 是"主工具"——agent 应该先调它，通常一个调用就能回答问题
- `codegraph_explore` 返回按文件分组的多个符号源码，避免 agent 链式调用多个 node
- 输出有 15000 字符上限（MAX_OUTPUT_LENGTH），防止 context 膨胀
- 输入有 10000 字符限制，防止恶意大 payload

### 3.3 导入解析系统

这是 CodeGraph 的**亮点模块**。不仅索引符号，还解析 import 语句，追踪跨文件引用链。

**支持 17 种框架识别**：React、Vue、Svelte、Express、NestJS、Laravel、Django、Go workspace、Rust workspace、Expo、Swift/ObjC bridge 等。

```typescript
const EXTENSION_RESOLUTION: Record<string, string[]> = {
  typescript: ['.ts', '.tsx', '.d.ts', '.js', '.jsx', '/index.ts', '/index.tsx'],
  python: ['.py', '/__init__.py'],
  rust: ['.rs', '/mod.rs'],
  java: ['.java'],
  // ...
};
```

处理了 tsconfig path aliases、Go module resolution、Rust mod.rs 等复杂场景。

### 3.4 文件监听 + 增量同步

- **chokidar** 监听文件变更，2000ms debounce
- **content_hash** 检测变化，只有文件内容真的变了才 reindex
- **跨进程锁**防止多个 MCP client 同时写索引
- **Git worktree 感知**：检测 worktree 切换，提示用户重新索引
- **Git hooks 可选**：在 watch 不可用时（如 WSL2），用 commit/checkout hook 触发同步

## 4. 设计决策分析

### 4.1 为什么用 SQLite 而不是 ChromaDB/向量数据库？

**CodeGraph 不用向量搜索。** 全部依赖 SQLite 的 FTS5 全文搜索 + 图遍历。

原因：
1. 符号搜索是**精确的**——搜 "UserService" 就是要找 UserService，不需要语义相似度
2. 图遍历是**结构化的**——BFS/DFS 查调用链，向量搜索做不到
3. SQLite 是**零依赖的**——CodeGraph 核心不依赖 Node.js 外的任何运行时
4. 性能足够——FTS5 在几十万条 nodes 上毫秒级响应

### 4.2 为什么用 web-tree-sitter（WASM）而不是 native binding？

- **零原生编译**——CodeGraph 自带 runtime，不需要用户安装任何 native 依赖
- **跨平台一致**——macOS/Linux/Windows 行为完全一样
- **牺牲了一点性能换取可移植性**——但对于一次性索引来说够用

### 4.3 为什么做 Daemon 而不是纯 stdio？

- Claude Code 等 agent **长期运行**，每次 query 都启动新进程太慢
- Daemon 保持索引在内存中，查询响应更快
- 支持 file watcher 持续同步

### 4.4 为什么做 Agent-Specific 的 Installer？

CodeGraph 有专门的 installer 为每个 agent（Claude Code、Cursor、Codex 等）写入不同的 MCP 配置。因为每个 agent 的 MCP 配置格式和 instructions 模板不同。

## 5. 竞品对比

| 维度 | CodeGraph | CodeKB | Sourcegraph | Greptile |
|------|-----------|--------|-------------|----------|
| **定位** | Agent 编程加速 | 代码知识库 | 企业代码搜索平台 | AI 代码理解 |
| **技术** | tree-sitter + SQLite + FTS5 | tree-sitter + SQLite + ChromaDB | language server + PostgreSQL | LLM + AST |
| **搜索** | FTS5 全文 + 图遍历 | 向量语义 + 结构化 | 语义 + 文本 + 引用 | 语义 |
| **语言** | 30 种 | 6 种 | 50+ 种 | 10+ 种 |
| **调用关系** | ✅ 完整调用图 + import resolution | ✅ 调用关系（简单） | ✅ | ✅ |
| **类型系统** | 13 种边类型（extends/implements/type_of） | 3 种（calls/contains/imports） | 丰富 | 有限 |
| **框架识别** | 17 种 | 0 | 企业级 | 有限 |
| **实时同步** | ✅ file watcher + git hooks | ❌ 手动 sync | ✅ | ✅ |
| **向量搜索** | ❌ | ✅ | ✅ | ✅ |
| **文档索引** | ❌ | ✅ docs/ + CLAUDE.md | ✅ | ✅ |
| **MCP** | ✅ 核心功能 | ✅ | ❌ | ✅ |
| **Agent 集成** | 8 种 agent | 通用 MCP | 企业 API | API |
| **零依赖** | ✅ 自带 runtime | ❌ 需要 Python + pip | ❌ SaaS | ❌ SaaS |
| **开源** | ✅ MIT | ✅ | ❌ | ❌ |

## 6. CodeKB 可借鉴的具体改进

### 🔴 P0：高优先级（直接提升价值）

#### 1. 增加 Edge 类型（CodeGraph 有 13 种，CodeKB 只有 3 种）

**现状**：CodeKB edges 只有 calls、contains（隐式）、imports
**借鉴**：增加 extends、implements、references、type_of、overrides、exports、instantiates

**具体改进**：
- `src/codekb/indexers/tree_sitter.py` 的 `extract_calls()` 方法名改为 `extract_edges()`
- 每个 Strategy 新增 `extract_inheritance()` 和 `extract_references()` 方法
- `sqlite_store.py` 的 edges 表增加 `kind` 字段

#### 2. Import Resolution 系统

**现状**：CodeKB 只记录 import 语句文本，不解析到目标文件/符号
**借鉴**：CodeGraph 的 `src/resolution/import-resolver.ts`

**具体改进**：
- 新增 `src/codekb/resolution/import_resolver.py`
- 支持 `@/` alias、`__init__.py`、Go module、Java package、Kotlin package 解析
- 在 sync 阶段 post-process unresolved imports

#### 3. 框架识别

**现状**：无
**借鉴**：CodeGraph 支持 17 种框架（React、Django、Express 等）

**具体改进**：
- 新增 `src/codekb/resolution/framework_detector.py`
- 至少支持：Django、FastAPI、Flask、Express、React、Spring Boot

#### 4. 文件监听 + 增量同步

**现状**：每次 `codekb sync --full` 全量重建
**借鉴**：CodeGraph 的 `src/sync/watcher.ts`

**具体改进**：
- `codekb watch` 命令：chokidar 等价物（watchdog 库）
- content_hash 变更检测
- 增量 reindex（只处理变更文件）

### 🟡 P1：中优先级

#### 5. `codegraph_context` 类似的"主工具"

CodeGraph 的核心洞察：给 agent 一个 **context 工具**而不是 search 工具。agent 描述任务，工具返回相关入口点 + 关键代码。

**改进**：CodeKB 的 `search_code` 改名为 `get_context`，接受自然语言任务描述，返回：
- 入口点符号
- 相关文件列表
- 关键代码片段（带行号）

#### 6. 查询语法增强

**现状**：纯语义向量搜索
**借鉴**：CodeGraph 的 `kind:function path:src name:auth` 过滤语法

**改进**：`search_code` 支持结构化过滤参数：
```
search_code(query="auth", kind="function", path="src/api")
```

#### 7. 自适应输出 Budget

**现状**：固定 top_k=10
**借鉴**：根据项目大小调整返回数量

#### 8. 代码输出限制

**现状**：无限制
**借鉴**：MAX_OUTPUT_LENGTH=15000，防止 context 膨胀

### 🟢 P2：低优先级

#### 9. Daemon 模式

CodeKB 的 MCP stdio 模式有 stdin 时序问题。借鉴 CodeGraph 的 daemon + unix socket 设计。

#### 10. Agent-Specific Instructions

为不同 agent 生成不同的 MCP instructions。

#### 11. Git Worktree 感知

检测 worktree 切换，避免使用过时索引。

#### 12. Unresolved References 表

记录无法解析的 import，后续 post-process。

## 7. 质量评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | 9/10 | 模块划分清晰，关注点分离好 |
| 代码质量 | 9/10 | TypeScript 严格模式，类型完整，注释详细 |
| 测试覆盖 | 8/10 | 40+ 测试文件，覆盖 extraction/resolution/mcp/sync |
| 文档质量 | 8/10 | README 详细，有 benchmark 数据，design docs |
| 可扩展性 | 9/10 | 语言策略、框架识别、MCP 工具都是可扩展的 |
| 性能考量 | 9/10 | 批量查询、LRU cache、自适应 budget、输出限制 |

**总分：8.7/10** — 质量非常高的开源项目

## 8. 总结

CodeGraph 是目前 **最成熟的面向 AI Agent 的代码知识图谱工具**。它的核心优势：

1. **30 种语言支持**（远超 CodeKB 的 6 种）
2. **完整的图关系**（13 种边类型 vs CodeKB 的 3 种）
3. **Import Resolution**（跨文件引用链追踪）
4. **实时同步**（file watcher + git hooks）
5. **零依赖**（自带 WASM runtime）
6. **Agent 优化设计**（context 主工具、自适应 budget、输出限制）

CodeKB 如果要提升竞争力，**最值得借鉴的 3 个方向**：
1. **Edge 类型扩充**（extends/implements/references/type_of）
2. **Import Resolution 系统**
3. **文件监听 + 增量同步**

但这不代表 CodeKB 没有差异化价值——CodeKB 的**向量语义搜索 + 文档索引 + LLM 生成**是 CodeGraph 没有的。两者的融合方向：**结构化图 + 语义搜索 + 文档知识 = 最强代码知识库**。
