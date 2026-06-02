# CodeKB

[English](README.md) | 中文

通用代码知识库系统，可索引 100+ 个代码仓库，并通过 MCP Server 为 AI 代理（Claude Code、Cursor 等）提供代码检索、文档查询、架构分析和代码生成能力。

## 系统架构

```
GitHub / GitLab / Gitee
        |  webhook push
        v
  +------------------+
  | Webhook 接收服务   |  (FastAPI)
  +--------+---------+
           |
           v
  +------------------+     +------------------+     +------------------+
  | 第3层: 文档       |     | 第2层: 结构索引    |    | 第1层: 向量索引    |
  | (Markdown)       |     | (SQLite)         |    | (ChromaDB)       |
  +------------------+     +------------------+     +------------------+
           |                        |                        |
           +------------------------+------------------------+
                                    |
                                    v
                          +------------------+
                          |   MCP Server     |  (stdio / SSE)
                          +------------------+
                                    |
                                    v
                          AI Agent / IDE
```

**4 层存储设计：**

| 层级 | 后端 | 内容 |
|------|------|------|
| 第 1 层 | ChromaDB | 语义向量嵌入（代码 + 文档） |
| 第 2 层 | SQLite | Tree-sitter 解析的结构数据（符号、调用图、导入） |
| 第 3a 层 | Markdown 文件 | LLM 生成的架构文档 |
| 第 3b 层 | Markdown 文件 | Agent 技能（可执行的任务级提示） |

**核心设计原则：** 查询时（MCP 运行时）零 LLM 调用。所有 LLM 处理在索引阶段完成，查询时纯粹走向量检索 + SQLite 查询。

## 快速开始

### 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 配置

```bash
cp .env.example .env
cp codekb.example.yaml codekb.yaml
# 编辑 .env 填入 API Key
# 编辑 codekb.yaml 配置参数
```

### 添加仓库

```bash
# 添加远程仓库
codekb repo add https://github.com/org/repo

# 添加本地仓库
codekb repo add --local /path/to/local/repo

# 指定别名
codekb repo add https://github.com/org/repo --name my-project
```

### 索引与同步

```bash
# 增量同步所有仓库
codekb sync

# 全量重新索引指定仓库
codekb reindex my-project

# 强制全量同步所有仓库
codekb sync --full
```

### MCP Server

```bash
# 启动 stdio MCP 服务（适用于 Claude Code 等 CLI 工具）
codekb serve

# 启动 SSE MCP 服务（适用于 Web 工具）
codekb serve --transport sse --port 8000
```

### 文档与技能

```bash
# 生成架构文档
codekb docs generate my-project

# 验证已生成的文档
codekb docs verify my-project

# 生成 Agent 技能
codekb skills generate my-project

# 列出所有技能
codekb skills list my-project
```

### Webhook 接收服务

```bash
# 启动 webhook 服务，接收 push 事件自动触发增量索引
codekb webhook --port 8080
```

## CLI 命令参考

```
codekb repo add <url> [--branch main] [--name alias] [--local]
codekb repo remove <name>
codekb repo list
codekb repo info <name>

codekb sync [--repo name] [--full]
codekb reindex <name>
codekb serve [--transport stdio|sse] [--port 8000]
codekb webhook [--port 8080]

codekb docs generate <name>
codekb docs verify <name>

codekb skills list <name>
codekb skills generate <name>
codekb skills verify <name>
codekb skills review <name> <skill> [--approve/--reject]
```

## MCP 工具列表

**17 个工具**，分为 4 个类别：

### 查询工具（核心入口）

| 工具 | 说明 |
|------|------|
| `query_usage` | 主要的「如何使用」查询工具 — 自动遵循文档优先链：README → API 指南 → 组件指南 → 用法示例 → 推荐 |
| `find_symbol` | 跨仓库搜索符号归属（查找符号所在的仓库和模块） |
| `resolve_keyword` | 解析模糊关键词为仓库/模块/符号类型 |
| `search_code` | 跨仓库语义代码搜索 |

### 管理与元数据

| 工具 | 说明 |
|------|------|
| `list_repos` | 列出所有已索引仓库 |
| `get_repo_info` | 获取仓库详细信息 |
| `list_modules` | 列出仓库中的模块（monorepo 支持） |
| `get_module_dependencies` | 跨模块依赖关系 |
| `get_structure` | 代码结构（类、函数、签名）；`repo_name` 可选 |
| `get_symbol_detail` | 符号完整定义 + 调用图；`repo_name` 可选 |
| `get_file_content` | 读取文件内容 |

### 文档与技能

| 工具 | 说明 |
|------|------|
| `get_architecture` | 获取生成的架构文档 |
| `get_code_template` | 获取项目代码模板 |
| `list_skills` | 列出可用的 Agent 技能 |
| `get_skill` | 获取技能完整内容 |
| `list_doc_index` | 列出文档索引（仅元数据） |
| `read_doc` | 按路径读取 Markdown 文档全文 |

## 项目结构

```
src/codekb/
  cli/main.py           # Typer CLI 命令行
  core/
    config.py           # YAML + .env 配置加载（多 Provider）
    repo_manager.py     # 仓库克隆、注册、元数据
    indexer.py          # 索引编排器（全量/增量）
  indexers/
    tree_sitter.py      # AST 解析（Python、JavaScript）
    embedder.py         # 向量嵌入流水线
    doc_generator.py    # LLM 文档生成 + 质量验证
    skill_generator.py  # 技能生成 + 自动验证
  storage/
    sqlite_store.py     # SQLite 元数据 + 结构存储（含 guide_cache）
    vector_store.py     # ChromaDB 向量操作
    doc_store.py        # Markdown 文件存储
  retrieval/
    semantic_search.py  # 向量语义搜索
    structure_query.py  # 结构化 SQLite 查询（含 find_symbol、resolve）
    hybrid_search.py    # RRF 混合搜索（向量 + 关键词）
    reference_builder.py # 用法示例、模板、指南
    guide_generator.py  # LLM 驱动的指南生成（组件/API/示例）
  mcp/
    server.py           # MCP 服务（17 个工具 + 资源）
    guide_cache.py      # 指南结果缓存层
  webhook/
    receiver.py         # FastAPI webhook 接收服务
    adapters/           # GitHub、GitLab、Gitee 适配器
```

## 技术栈

| 组件 | 选型 | 原因 |
|------|------|------|
| 语言 | Python 3.11+ | MCP SDK 支持，生态成熟 |
| CLI | Typer | 类型安全，自动补全 |
| Web 框架 | FastAPI | Webhook 接收，异步支持 |
| MCP SDK | mcp[cli] | Anthropic 官方 Python SDK |
| 代码解析 | tree-sitter | 多语言 AST 解析，行业标准 |
| 向量数据库 | ChromaDB | 轻量级嵌入，无需额外服务 |
| 结构化存储 | SQLite | 轻量级，单文件 |
| LLM 接口 | litellm | 统一接口支持所有 LLM 提供商 |
| 嵌入模型 | 可插拔 | 默认 sentence-transformers，可选 OpenAI |

**多 Provider 配置** — `codekb.yaml` 支持通过 litellm 格式配置多个 LLM/Embedding 提供商：

```yaml
codekb:
  llm_providers:
    openai:
      provider: openai
      base_url: https://api.openai.com/v1
      model: gpt-4o-mini
    deepseek:
      provider: openai
      base_url: https://api.deepseek.com/v1
      model: deepseek-chat
  assignments:
    doc_generation: openai
```
| 配置管理 | pydantic-settings | 类型安全，.env 支持 |

## 运行测试

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

共 86 个测试，覆盖配置、存储、tree-sitter 解析、guide 缓存/生成、webhook 和完整集成流程。

## 许可证

MIT
