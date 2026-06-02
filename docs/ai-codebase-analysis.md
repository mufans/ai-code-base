# CodeKB (ai-code-base) 深度分析报告

> 基于 docs/codegraph-analysis-report.md 的 CodeGraph 分析，对 CodeKB 项目做对比分析和竞争力评估。

## 1. 项目现状梳理

### 1.1 技术架构

```
源代码 → Tree-sitter 解析 → 符号提取 → SQLite + ChromaDB → MCP Server → AI Agent
            ↓                    ↓              ↓                ↓
      8 种语言策略          Symbol/Call/Import  向量+FTS5混合     17 个工具
```

**存储层**（4 层）：
| 层 | 技术 | 内容 |
|---|---|---|
| 元数据 | SQLite (metadata.db) | repo 信息、索引状态、guide 缓存 |
| 结构 | SQLite (structure.db) | symbols、calls、imports、file tree、doc index |
| 向量 | ChromaDB | 代码 embedding + 文档 embedding |
| 文件 | Markdown | 生成的架构文档、skills |

### 1.2 MCP 工具清单（17 个）

**核心查询入口**（2 个）：
- `query_usage` — 主入口，doc-first 优先链（README → API guide → component guide → usage examples → recommend）
- `search_code` — 语义代码搜索

**跨仓库定位**（2 个）：
- `find_symbol` — 按符号名查找所属 repo/module
- `resolve_keyword` — 消歧：判断关键词是 repo/module/symbol

**结构查询**（4 个）：
- `get_structure` — 代码结构（类、函数、签名），支持 summary/detail 两种模式
- `get_symbol_detail` — 符号完整定义 + 调用图
- `get_file_content` — 读取文件内容
- `get_architecture` — 生成的架构文档

**仓库管理**（4 个）：
- `list_repos`、`get_repo_info`、`list_modules`、`get_module_dependencies`

**文档与技能**（5 个）：
- `list_doc_index`、`read_doc`、`list_skills`、`get_skill`、`get_code_template`

### 1.3 语言支持（8 种）

Python、JavaScript/JSX、TypeScript/TSX、Java、Kotlin、Swift、ArkTS、Go

### 1.4 边类型（3 种）

- `calls` — 函数调用关系（CallRelation）
- `imports` — 模块导入（ImportRecord）
- `contains` — 隐式的 parent 字段表示包含关系

---

## 2. 对比 CodeGraph：具体差距分析

### 2.1 Edge 类型

| 维度 | CodeGraph | CodeKB |
|------|-----------|--------|
| 边类型数量 | 13 种 | 3 种 |
| 继承 | `extends` | 无 |
| 接口实现 | `implements` | 无 |
| 类型引用 | `type_of` | 无 |
| 重写 | `overrides` | 无 |
| 导出 | `exports` | 仅有 `is_exported` 标记 |
| 引用 | `references` | 无 |
| 实例化 | `instantiates` | 无 |

**实际影响**：CodeKB 无法回答 "哪些类继承了 BaseService？"、"这个接口有哪些实现？" 等问题。对于 OOP 项目（Java/Spring、Kotlin/Android），这是一个重大缺陷。

### 2.2 Import Resolution

| 维度 | CodeGraph | CodeKB |
|------|-----------|--------|
| 记录 import 文本 | 有 | 有 |
| 解析到目标文件/符号 | 有（完整的 import resolver） | 无 |
| 跨文件引用链追踪 | 有 | 无 |
| 框架感知解析 | 17 种框架 | 无 |
| 路径别名处理 | tsconfig paths、Go module 等 | 无 |

**实际影响**：CodeKB 的 `ImportRecord` 只记录 `module: "os"` 或 `module: ".utils"` 这样的原始文本，没有解析到实际的文件路径或符号 ID。Agent 无法通过 import 链追踪依赖关系。

### 2.3 文件监听/增量同步

| 维度 | CodeGraph | CodeKB |
|------|-----------|--------|
| 实时文件监听 | chokidar + 2000ms debounce | 无 |
| 增量 reindex API | 自动触发 | `incremental_index()` 方法存在但需手动调用 |
| content_hash 变更检测 | 有 | 无（全量删除再插入） |
| Git hooks 集成 | 有 | 无 |
| 跨进程锁 | 有 | 无 |

**实际影响**：CodeKB 的 `IndexOrchestrator.incremental_index()` 方法已实现增量逻辑（接受 changed_files 列表），但缺少文件监听触发机制。Agent 查询时可能使用过时的索引。

### 2.4 MCP 工具设计

| 维度 | CodeGraph | CodeKB |
|------|-----------|--------|
| 工具数量 | 10 | 17 |
| "主工具"设计 | `codegraph_context`（任务描述 → 自动探索） | `query_usage`（doc-first 优先链） |
| 输出限制 | 15000 字符上限 | 有（guide 系统 6000-15000 不等） |
| 自适应 budget | 根据项目规模调整 | 固定 top_k=10 |
| 批量探索 | `codegraph_explore` 按文件分组 | 无 |
| 调用链追踪 | `codegraph_trace` | 无 |
| 影响分析 | `codegraph_impact` | 无 |
| Agent instructions | 简洁（~200 字） | 完整（多段描述） |

**分析**：CodeKB 的工具更多但缺少几个关键的高级工具（trace、impact）。`query_usage` 的 doc-first 设计有差异化价值，但它不像 `codegraph_context` 那样能基于任务描述自动探索代码结构。

### 2.5 搜索能力

| 维度 | CodeGraph | CodeKB |
|------|-----------|--------|
| 搜索方式 | FTS5 全文 + 图遍历 | 向量语义 + 关键词 + RRF 融合 |
| 语义搜索 | 无 | 有（ChromaDB + embedding） |
| 结构化过滤 | `kind:function path:src name:auth` | 仅有 repo/module 过滤 |
| 图遍历 | BFS/DFS 完整支持 | 无（仅有 caller/callee 列表） |

**分析**：这是 CodeKB 最核心的差异化优势。向量语义搜索让 Agent 能用自然语言查找代码（如 "处理用户认证的逻辑"），这比精确符号匹配灵活得多。但缺少结构化过滤（按 kind、path 过滤）限制了搜索精度。

### 2.6 语言支持

| CodeGraph | CodeKB |
|-----------|--------|
| 30 种（web-tree-sitter WASM） | 8 种（native tree-sitter bindings） |

CodeKB 缺少：C/C++、Rust、Ruby、PHP、Scala、C#、Dart、Lua、Elixir 等。不过 CodeKB 有 ArkTS（鸿蒙）支持，这是 CodeGraph 没有的。

### 2.7 框架识别

| CodeGraph | CodeKB |
|-----------|--------|
| 17 种（React、Vue、Django、Express、Spring 等） | 无 |

CodeGraph 基于框架特征做 import resolution 和符号关联，CodeKB 没有这部分能力。

---

## 3. CodeKB 的差异化优势

### 3.1 向量语义搜索（CodeGraph 完全没有）

CodeKB 的 HybridSearch 实现了：
- 代码向量搜索（ChromaDB）
- 文档向量搜索
- 关键词匹配（BM25-like）
- Reciprocal Rank Fusion 融合排序

这意味着 Agent 可以用模糊的自然语言描述找到代码，而不仅仅是精确的符号名匹配。

### 3.2 Guide 系统（CodeGraph 完全没有）

CodeKB 有 4 个 LLM 驱动的 guide 工具：
- **Component Guide** — 某个类/函数的使用指南，含属性、方法、示例
- **Usage Examples** — 真实代码中的使用模式
- **Recommend Component** — 根据需求推荐组件
- **API Guide** — 库/模块的集成指南

配合 `GuideCache` 缓存机制避免重复 LLM 调用。这是 CodeGraph 完全不具备的能力。

### 3.3 文档索引（CodeGraph 完全没有）

- 索引 README、CLAUDE.md、architecture docs 等文档
- 文档 embedding 支持语义搜索
- `list_doc_index` + `read_doc` 工具让 Agent 直接访问项目文档

### 3.4 query_usage 的 doc-first 优先链

`query_usage` 实现了智能的降级策略：README → API guide → component guide → usage examples → recommendation。这让 Agent 在大多数情况下一次调用就能得到有用信息，减少 tool call 次数。

### 3.5 跨仓库搜索

`find_symbol` + `resolve_keyword` 支持在多个索引仓库中搜索符号，CodeGraph 主要面向单项目场景。

### 3.6 Skill 系统

CodeKB 能为项目生成 agent skills（可复用的操作指南），这是 CodeGraph 没有的概念。

### 3.7 Monorepo 支持

`ModuleInfo` + `detect_modules` 支持自动检测 monorepo 子模块，每个模块独立索引和查询。

---

## 4. 客观评价：竞争力评估

### 4.1 优势总结

| 优势 | 价值级别 | 说明 |
|------|----------|------|
| 向量语义搜索 | **高** | 能用自然语言查代码，CodeGraph 做不到 |
| Guide 系统 | **高** | LLM 生成的使用指南 + 缓存，Agent 体验显著提升 |
| 文档索引 | **中** | README/CLAUDE.md 语义搜索，减少 grep |
| doc-first query_usage | **中** | 智能降级链，减少 tool call |
| 跨仓库搜索 | **中** | 多仓库统一查询 |
| Monorepo 支持 | **低** | 有但 CodeGraph 也有 |
| ArkTS 支持 | **低** | 鸿蒙生态，小众但有差异化 |

### 4.2 不足总结

| 不足 | 严重程度 | 说明 |
|------|----------|------|
| Edge 类型只有 3 种 | **高** | 无法追踪继承/实现/引用关系，OOP 项目场景受限 |
| 无 Import Resolution | **高** | 无法追踪跨文件依赖链 |
| 无文件监听 | **中** | 索引可能过时，需手动 sync |
| 语言支持 8 种 | **中** | CodeGraph 30 种，差距 4 倍 |
| 无框架识别 | **中** | 无法利用框架约定优化搜索 |
| 无调用链追踪（trace） | **中** | 无法回答 "从入口到 DB 这条调用链经过哪些函数" |
| 无影响分析（impact） | **低** | 重构前评估受限 |
| 依赖 Python 运行时 | **低** | CodeGraph 自带 WASM runtime，零原生依赖 |
| 无输出 budget 自适应 | **低** | 大项目可能返回过多内容 |

### 4.3 定位差异

| 维度 | CodeGraph | CodeKB |
|------|-----------|--------|
| 核心能力 | **结构化图查询**（精确、快速） | **语义搜索 + 知识生成**（智能、丰富） |
| 适合场景 | "UserService 被谁调用了？" | "这个项目怎么用认证功能？" |
| Agent 价值 | 减少 tool call、加速精确查询 | 提供深度文档和使用指南 |
| 技术路线 | 精确索引（FTS5 + 图） | 语义索引（向量 + LLM） |

**结论**：两者不是直接竞品，而是**互补关系**。CodeGraph 在结构化查询上更强，CodeKB 在语义理解和文档生成上更强。

---

## 5. 改进建议（按优先级）

### P0：高优先级（直接补齐核心短板）

#### 1. 扩充 Edge 类型 → 支持继承和接口关系

**当前问题**：`CallRelation` 模型只支持调用关系，schema 中没有继承/实现等概念。

**具体方案**：
- 在 `sqlite_store.py` 中新增 `EdgeRelation` 模型，替代 `CallRelation`
- 字段：`source_symbol, target_symbol, kind(calls/extends/implements/references/overrides/type_of), line_number, repo_name`
- 每个语言策略新增 `extract_inheritance()` 方法
- Python：`class Foo(Bar)` → extends 边
- Java/Kotlin：`extends`/`implements` 关键字 → 对应边
- TypeScript：`interface` + `implements` → implements 边

#### 2. Import Resolution 系统

**当前问题**：`ImportRecord.module` 只是原始文本，没有解析到实际文件。

**具体方案**：
- 新增 `src/codekb/resolution/import_resolver.py`
- 解析策略：
  - Python：相对导入 → 基于 `__init__.py` 的包路径解析
  - TypeScript：路径别名 → 读取 tsconfig.json 的 paths 配置
  - Java/Kotlin：包名 → 目录结构映射
  - Go：module path → go.mod 解析
- 在索引阶段 post-process：对每个 `ImportRecord` 尝试匹配到 `Symbol`

#### 3. 文件监听触发机制

**当前问题**：`incremental_index()` 已实现但无触发。

**具体方案**：
- 新增 `codekb watch` CLI 命令
- 使用 `watchdog` 库（Python 生态的 chokidar 等价物）
- 2000ms debounce，content_hash 变更检测
- 或更简单的方案：MCP Server 启动时检查 repo 的 git status，自动增量更新变更文件

### P1：中优先级（提升查询能力）

#### 4. 调用链追踪（trace）工具

类似 CodeGraph 的 `codegraph_trace`，实现 BFS/DFS 遍历调用图。

**具体方案**：
- 在 `structure_query.py` 新增 `trace_call_chain()` 方法
- 参数：`symbol_name, direction(upstream/downstream), max_depth`
- 返回完整的调用链路径

#### 5. 影响分析（impact）工具

重构前评估 "改了这个函数会影响哪些代码"。

**具体方案**：
- 基于已有的 `get_symbol_detail`（caller 列表）扩展
- 递归收集所有下游调用者
- 按模块/文件分组展示影响范围

#### 6. 结构化搜索过滤

**当前问题**：`search_code` 只支持 repo/module 过滤。

**具体方案**：
- `search_code` 新增 `kind` 参数（function/class/method/interface）
- `search_code` 新增 `path` 参数（文件路径前缀过滤）
- 在 hybrid search 的 keyword 阶段做过滤

#### 7. 输出 budget 自适应

**当前问题**：固定 top_k=10，大项目可能返回太多/太少。

**具体方案**：
- 根据项目文件数动态调整 top_k
- 参考 CodeGraph 的 `getExploreBudget()`
- 添加全局 MAX_OUTPUT_LENGTH 限制

### P2：低优先级（锦上添花）

#### 8. 框架识别

检测 React、Django、FastAPI、Spring Boot 等，用于优化搜索和 import resolution。

#### 9. 扩展语言支持

优先考虑 C/C++、Rust（系统编程常用）、Dart（Flutter 生态）。

#### 10. Agent-Specific 配置

为 Claude Code、Cursor、Codex 等生成不同的 MCP instructions 模板。

#### 11. Daemon 模式

长期运行的 daemon + unix socket，而非每次启动新进程。

---

## 6. 最终评价

### 6.1 项目成熟度

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | 7/10 | 4 层存储清晰，模块化好，但 Edge 模型过于简单 |
| 代码质量 | 7/10 | Pydantic v2 + 类型注解，风格一致，但部分模块耦合度偏高 |
| 测试覆盖 | 5/10 | 有 tests/ 但覆盖范围不明，guide 系统、MCP 工具缺少集成测试 |
| 文档质量 | 6/10 | CLAUDE.md 详细，但面向用户的文档不足，无 benchmark 数据 |
| 可扩展性 | 7/10 | 语言策略、embedding provider 可扩展，但 Edge 类型和 import 不易扩展 |
| 差异化价值 | 8/10 | 向量搜索 + Guide 系统 + 文档索引是独特组合 |

### 6.2 与 CodeGraph 对比总评

| 维度 | CodeGraph | CodeKB |
|------|-----------|--------|
| 核心技术深度 | **9/10**（图遍历、import resolution、30 种语言） | **7/10**（向量搜索好，但图能力弱） |
| Agent 体验设计 | **8/10**（context 主工具、自适应 budget） | **7/10**（query_usage 有亮点，但缺少 trace/impact） |
| 差异化能力 | **6/10**（纯结构化查询） | **8/10**（语义搜索 + LLM 生成） |
| 工程完整度 | **9/10**（daemon、watcher、跨进程锁、benchmark） | **5/10**（无文件监听、无 benchmark、无 daemon） |
| 实用性 | **9/10**（npm install 即用、零配置） | **6/10**（需要 Python + pip + embedding 配置） |

### 6.3 核心结论

CodeKB 不是一个"更差的 CodeGraph"，而是一个**不同路线的产品**：

- **CodeGraph 的路线**：精确的结构化图谱 → 让 Agent 快速定位代码
- **CodeKB 的路线**：语义搜索 + LLM 生成 → 让 Agent 深度理解代码

CodeKB 的独特价值在于 **向量语义搜索 + Guide 生成系统 + 文档索引** 这个三合一组合，这是 CodeGraph 完全没有的。但 CodeKB 在**结构化图查询**（Edge 类型、import resolution、调用链追踪）上差距明显。

**最有价值的改进方向**：不是去追赶 CodeGraph 的全部能力，而是：
1. 补齐最核心的图查询短板（Edge 类型 + import resolution）
2. 保持和强化语义搜索 + Guide 生成这个差异化优势
3. 最终形成 **"结构化图 + 语义搜索 + LLM 文档"** 三位一体的能力

这样的 CodeKB 才能真正做到报告中所说的：**结构化图 + 语义搜索 + 文档知识 = 最强代码知识库**。
