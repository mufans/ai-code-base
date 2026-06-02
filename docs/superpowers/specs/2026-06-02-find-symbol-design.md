# find_symbol: 跨仓库符号查找与自动定位

**日期**: 2026-06-02
**状态**: 已批准

## 背景

CodeKB 的 MCP 工具（`query_usage`、`get_symbol_detail`、`get_structure`）要求 `repo_name` 为必填参数。当用户只知符号名（如 `QuoteService`）而不知其归属仓库和模块时，无法直接查询。现有 `search_code` 虽支持跨仓库搜索，但它是语义搜索，不适合精确符号定位。

## 目标

1. 新增 `find_symbol` 工具，支持跨所有仓库精确查找符号归属
2. 让 `query_usage`、`get_symbol_detail`、`get_structure` 的 `repo_name` 变为可选，缺失时自动定位

## 设计

### 1. 新工具：`find_symbol`

**功能**：跨所有仓库精确匹配符号名，返回归属和基础信息。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `symbol_name` | string | 是 | 符号名（精确匹配） |

**返回示例**：

```json
{
  "results": [
    {
      "repo_name": "HarmonyContainer",
      "module": "quote_service",
      "file_path": "bizBase/quote_service/QuoteService.ets",
      "name": "QuoteService",
      "kind": "class",
      "signature": "export class QuoteService { ... }",
      "line": 15,
      "end_line": 120,
      "docstring": "行情服务类..."
    }
  ],
  "total": 1
}
```

找不到时返回 `{"results": [], "total": 0}`。

### 2. 自动定位机制

**受影响工具**：`query_usage`、`get_symbol_detail`、`get_structure`

**规则**：
- `repo_name` 从 `required` 改为 `optional`
- 当 `repo_name` 未提供时，内部调用 `_resolve_symbol` 搜索符号
- 找到 1 个仓库：自动使用，继续执行
- 找到 0 个仓库：返回错误提示
- 找到多个仓库：返回候选列表，提示指定 `repo_name`

### 3. 存储层

#### `SqliteStore` 新增方法

```python
def find_symbol_across_repos(self, name: str) -> list[Symbol]:
    """跨所有仓库精确搜索符号名。"""
    conn = self._connect(self._struct_path)
    rows = conn.execute(
        "SELECT * FROM symbols WHERE name = ? ORDER BY repo_name, repo_module",
        (name,),
    ).fetchall()
    conn.close()
    return [Symbol(**dict(r)) for r in rows]
```

#### `StructureQuery` 新增方法

```python
def find_symbol(self, symbol_name: str) -> list[dict]:
    """跨仓库查找符号归属，返回精简 dict 列表。"""

def resolve_symbol(self, name: str) -> Optional[tuple[str, Optional[str]]]:
    """解析符号到 (repo_name, module)，用于自动定位。
    唯一匹配时返回结果，多个或零个匹配返回 None。
    """
```

### 4. MCP Server 改动

- `list_tools` 中新增 `find_symbol` 工具定义
- `query_usage`、`get_symbol_detail`、`get_structure` 的 `inputSchema` 中 `repo_name` 从 `required` 移除
- `_handle_tool` 中对这 3 个工具增加 `repo_name` 缺失时的自动定位逻辑：
  1. 从 `query` 或 `symbol_name` 参数提取符号名
  2. 调用 `structure_query.resolve_symbol` 定位
  3. 根据匹配数量决定行为
- `_CODEKB_INSTRUCTIONS` 更新，说明 `find_symbol` 的用途

### 5. 测试

- `SqliteStore` 单测：`find_symbol_across_repos` 在多仓库数据下的正确性
- MCP 集成测试：`find_symbol` 工具调用、自动定位行为（0/1/N 个匹配）

## 涉及文件

| 文件 | 改动 |
|------|------|
| `src/codekb/storage/sqlite_store.py` | 新增 `find_symbol_across_repos` |
| `src/codekb/retrieval/structure_query.py` | 新增 `find_symbol`、`resolve_symbol` |
| `src/codekb/mcp/server.py` | 注册新工具、修改 3 个工具 schema、添加自动定位逻辑 |
