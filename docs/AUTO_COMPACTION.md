# 自动上下文压缩功能

## 概述

当单轮对话的上下文接近 256k tokens 时，自动压缩前 80% 的对话内容为摘要，保留最近 20% 的完整消息，确保长对话能够持续进行。

## 工作原理

```
原始对话（300k tokens）:
┌─────────────────────────────────────────────┐
│ System Prompt                                │
│ ────────────────────────────────────────── │
│ [消息 1-100] ← 前 80% 压缩为摘要             │
│ ────────────────────────────────────────── │
│ [消息 101-125] ← 最近 20% 完整保留           │
└─────────────────────────────────────────────┘

压缩后（约 100k tokens）:
┌─────────────────────────────────────────────┐
│ System Prompt                                │
│ ────────────────────────────────────────── │
│ ## 早期对话摘要                              │
│ - 用户意图：...                              │
│ - 完成操作：...                              │
│ - 当前状态：...                              │
│ ────────────────────────────────────────── │
│ [消息 101-125] ← 完整保留                    │
└─────────────────────────────────────────────┘
```

## 核心配置

- **触发阈值**: 256,000 tokens
- **压缩比例**: 前 80% 压缩为摘要
- **保留比例**: 最近 20% 完整保留
- **摘要模型**: claude-3-haiku-20240307（可通过环境变量 `SUMMARIZE_MODEL` 配置）

## 实现细节

### 1. 自动检测与触发

**位置**: `agent_loop.py:call_model()`

每次调用模型前自动检查上下文大小：

```python
from context_manager.compaction import should_compact

if should_compact(messages, threshold_tokens=256000):
    # 自动触发压缩
    ...
```

### 2. 压缩策略

**位置**: `context_manager/compaction.py`

```python
def compact_messages(messages, keep_ratio=0.2):
    """
    压缩消息历史

    Args:
        messages: 原始消息列表
        keep_ratio: 保留最近消息的比例（默认 0.2）

    Returns:
        (压缩后的消息, 待总结内容)
    """
```

**压缩过程**:
1. 计算分割点：`split_idx = int(len(messages) * 0.8)`
2. 提取前 80% 的消息内容
3. 保留最近 20% 的完整消息
4. 生成摘要占位符

### 3. 摘要生成

**位置**: `context_manager/summarizer.py`

使用小模型（claude-3-haiku）生成高质量摘要：

```python
async def generate_summary(content: str) -> str:
    """
    使用小模型生成摘要

    摘要内容包括：
    - 用户主要意图和目标
    - 已完成的关键操作
    - 重要决策和理由
    - 当前阻塞点
    - 用户偏好
    """
```

**Fallback 机制**:
- 模型调用失败时，使用规则摘要（提取前 10 条和最后 5 条）
- 超时设置：15 秒
- Token 限制：500 tokens

## 使用示例

### 场景 1: 长期对话

```python
# 用户经过 100 轮对话后...
messages = [
    {"role": "system", "content": "你是一个编程助手"},
    # ... 200 条消息 ...
]

# 第 101 轮对话时，上下文达到 260k tokens
# 自动触发压缩

# 输出:
# ⚙️  上下文接近 256k 限制，正在压缩前 80% 的对话...
# ✓ 已压缩，估算节省 180000 tokens

# 压缩后的消息结构:
messages = [
    {"role": "system", "content": "你是一个编程助手"},
    {"role": "system", "content": "## 早期对话摘要\n\n用户要求实现..."},
    # ... 最近 40 条完整消息 ...
]
```

### 场景 2: 大文件操作

```python
# 用户读取多个大文件
messages = [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "读取 large_file_1.json"},
    {"role": "tool", "name": "read_file", "content": "... 50k tokens ..."},
    {"role": "user", "content": "读取 large_file_2.json"},
    {"role": "tool", "name": "read_file", "content": "... 60k tokens ..."},
    # ... 继续读取更多文件 ...
]

# 当累积到 260k tokens 时自动压缩
# 早期的文件内容被总结为: "读取了多个配置文件，提取了关键配置项..."
# 保留最近的文件内容完整
```

## 配置选项

### 环境变量

```bash
# .env 文件

# 摘要模型（可选，默认使用 claude-3-haiku-20240307）
SUMMARIZE_MODEL=claude-3-haiku-20240307

# 或使用其他小模型
SUMMARIZE_MODEL=gpt-3.5-turbo
SUMMARIZE_MODEL=deepseek-chat
```

### 代码配置

**调整阈值**:

```python
# agent_loop.py

# 改为 200k tokens 触发
if should_compact(messages, threshold_tokens=200000):
    ...
```

**调整保留比例**:

```python
# agent_loop.py

# 保留最近 30% 的消息
compacted_messages, summary_text = compact_messages(messages, keep_ratio=0.3)
```

## 性能影响

### Token 节省

**典型场景** (300k → 100k):
- 原始上下文: 300,000 tokens
- 压缩后: 约 100,000 tokens
- **节省: ~200,000 tokens (67%)**

### 时间开销

- **Token 估算**: < 1ms
- **消息重组**: < 10ms
- **摘要生成**: 2-5 秒（使用 claude-3-haiku）
- **总耗时**: 约 2-5 秒（用户有感知但可接受）

### 成本

- **摘要模型调用**: 1 次 (输入 ~2k tokens, 输出 ~500 tokens)
- **claude-3-haiku 成本**: 约 $0.0015 per 压缩
- **主模型节省**: 后续每轮对话节省 200k tokens

**ROI**: 压缩 1 次可节省后续 5-10 轮对话的 token 成本

## 注意事项

### ✅ 优势

1. **无缝体验**: 自动触发，用户无需手动干预
2. **保留上下文**: 最近 20% 完整保留，模型能看到最新状态
3. **智能摘要**: 使用小模型生成高质量摘要
4. **成本效益**: 大幅降低后续对话的 token 消耗

### ⚠️ 限制

1. **信息丢失**: 前 80% 的细节被压缩，模型无法看到完整内容
2. **摘要质量**: 依赖小模型的总结能力，可能遗漏重要细节
3. **时间延迟**: 首次压缩需要 2-5 秒等待
4. **不适用场景**: 需要完整历史的任务（如代码重构、复杂调试）

### 🔧 适用场景

✅ **适合**:
- 长期对话（>50 轮）
- 多文件操作
- 知识问答
- 日常编程辅助

❌ **不适合**:
- 复杂重构（需要完整上下文）
- 大型调试会话（需要追溯早期步骤）
- 精确的代码审查

## 手动控制

如果需要禁用自动压缩，可以修改 `agent_loop.py`:

```python
# 注释掉自动压缩逻辑
# if should_compact(messages, threshold_tokens=256000):
#     ...
```

或设置极高的阈值：

```python
if should_compact(messages, threshold_tokens=10000000):  # 10M tokens, 永远不触发
    ...
```

## 未来改进

1. **渐进式压缩**: 每次只压缩 20%，而不是一次性压缩 80%
2. **重要性评分**: 根据消息重要性选择性压缩，而非按时间顺序
3. **用户确认**: 压缩前询问用户是否同意
4. **压缩历史**: 记录压缩点，支持查看原始历史
5. **可配置策略**: 通过配置文件自定义压缩比例和阈值

## 测试

运行测试验证功能：

```bash
PYTHONPATH=. python3 tests/test_compaction.py
```

预期输出：
```
✓ 所有测试通过
```

## 相关文件

- `context_manager/compaction.py` - 压缩逻辑
- `context_manager/summarizer.py` - 摘要生成
- `agent_loop.py` - 集成点
- `tests/test_compaction.py` - 单元测试
- `docs/MEMORY_LAYER.md` - Memory 和会话摘要设计
- `docs/PROJECT_ARCHITECTURE_STATUS.md` - 当前架构状态
