# FileSearch 原理详解

## 🎯 核心概念

**FileSearch = 模糊匹配 + 相关性评分 + 智能排序**

类似于 VS Code 的 `Ctrl+P` 或 Sublime Text 的 `Cmd+P`

---

## 🔧 工作原理

### 1. 模糊匹配算法

**不需要精确匹配**，只要部分字符匹配即可：

```
查询: "usrmdl"
匹配:
  ✓ "user_model.py"     (u-s-r-m-d-l)
  ✓ "UserModule.ts"     (U-s-r-M-d-l)
  ✓ "usr_mdl_test.py"   (usr-mdl)
```

**算法**:
1. **子序列匹配**: 查询字符在文件名中按顺序出现
2. **距离计算**: 匹配字符之间的距离越小越好
3. **权重**: 连续匹配、大小写匹配、路径开头匹配有更高权重

---

### 2. 相关性评分

**多个因素综合评分**:

```python
score = (
    base_match_score +      # 基础匹配度 (0-100)
    consecutive_bonus +     # 连续字符加分
    case_match_bonus +      # 大小写完全匹配加分
    path_start_bonus +      # 文件名开头匹配加分
    file_type_bonus -       # 重要文件类型加分
    depth_penalty           # 深层目录减分
)
```

**示例**:
```
查询: "user"

文件                         评分    原因
────────────────────────────────────────────
user.py                     95      完全匹配 + 短路径
models/user.py              90      完全匹配 + 模型文件
tests/test_user.py          85      包含完整词 + 测试文件
src/utils/user_helper.py    70      包含但不在开头
node_modules/user.js        40      在排除目录
```

---

### 3. 智能过滤

**自动排除无关文件**:
```python
exclude = {
    # 版本控制
    ".git", ".svn", ".hg",

    # 虚拟环境
    ".venv", "venv", "env",

    # 依赖
    "node_modules", "vendor",

    # 构建产物
    "__pycache__", "dist", "build", ".next",

    # 临时文件
    "*.pyc", ".DS_Store", "*.log"
}
```

---

## 📊 算法示例

### 示例 1: 基础模糊匹配

```python
query = "usmd"
file = "user_model.py"

# 1. 找到匹配位置
#    u s e r _ m o d e l . p y
#    ^   ^     ^ ^            → 匹配 u-s-m-d

# 2. 计算距离
gaps = [2, 3, 1]  # u到s距离2, s到m距离3, m到d距离1
avg_gap = (2 + 3 + 1) / 3 = 2

# 3. 计算评分
base_score = 70  # 所有字符都匹配
gap_penalty = avg_gap * 2 = 4
consecutive_bonus = 10  # "m-o-d" 连续

final_score = 70 - 4 + 10 = 76
```

---

### 示例 2: 路径匹配优化

```python
query = "user"

# 候选文件
files = [
    "user.py",                    # A
    "models/user.py",            # B
    "src/utils/user_helper.py",  # C
]

# 评分计算
A: 完全匹配 + 根目录 + 短名 = 95
B: 完全匹配 + models目录 = 90
C: 部分匹配 + 深路径 = 70

# 排序结果
1. user.py (95)
2. models/user.py (90)
3. src/utils/user_helper.py (70)
```

---

## 🚀 常用库对比

### 1. **Fuse.js** (JavaScript)
- 纯 JS 实现
- 配置灵活
- 性能中等

### 2. **rapidfuzz** (Python) ⭐ 推荐
- C++ 实现，速度极快
- API 简单
- 支持多种算法

```python
from rapidfuzz import fuzz

# 部分匹配
fuzz.partial_ratio("user", "user_model.py")  # 100

# 排序匹配（忽略顺序）
fuzz.token_sort_ratio("model user", "user_model")  # 100

# 加权匹配
fuzz.WRatio("usr", "user_model.py")  # 86
```

### 3. **nucleo** (Rust) - Codex 使用
- 极致性能
- 实时增量搜索
- 但只有 Rust 版本

---

## 💡 实现策略

### 简单版本 (Day 2)

**使用 rapidfuzz**:

```python
from rapidfuzz import fuzz, process

def file_search(query: str, max_results: int = 20):
    # 1. 获取所有文件（缓存）
    all_files = _get_all_files_cached()

    # 2. 过滤明显无关的
    candidates = [f for f in all_files if not _should_exclude(f)]

    # 3. 计算相关性
    scored = []
    for file in candidates:
        score = fuzz.partial_ratio(query.lower(), file.name.lower())
        if score > 60:  # 阈值
            scored.append({"path": str(file), "score": score})

    # 4. 排序并返回
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:max_results]
```

**优势**:
- 简单易懂
- 性能足够（<100ms for 1000+ files）
- 依赖少（只需 rapidfuzz）

---

### 完整版本 (未来)

**高级特性**:

```python
def file_search_advanced(query: str):
    # 1. 权重计算
    def calculate_score(file, matches):
        base = matches.score

        # 路径深度惩罚
        depth_penalty = file.parts.count() * 2

        # 文件类型加分
        type_bonus = 10 if file.suffix in [".py", ".js"] else 0

        # 连续匹配加分
        consecutive = _count_consecutive(matches.indices)
        consecutive_bonus = consecutive * 5

        return base - depth_penalty + type_bonus + consecutive_bonus

    # 2. 缓存优化
    @lru_cache(maxsize=1)
    def get_file_list():
        return list(Path(".").rglob("*"))

    # 3. 增量搜索
    # 用户输入 "u" → "us" → "use" 时复用之前的结果
```

---

## 🎯 算法选择

### 场景 1: 精确子串
```python
"user" in "user_model.py"  # True
score = 100
```

### 场景 2: 模糊子序列
```python
"usmd" 在 "user_model.py" 中按顺序出现
score = fuzz.partial_ratio("usmd", "user_model.py")  # ~76
```

### 场景 3: 缩写匹配
```python
"UMP" 匹配 "UserModelProvider"
# 匹配大写字母: U-M-P
score = _abbreviation_match("UMP", "UserModelProvider")  # 90
```

---

## 📊 性能数据

**rapidfuzz 性能** (1000 个文件):

```
查询一次: ~50ms
- 遍历: 10ms
- 评分: 30ms
- 排序: 10ms

优化后 (缓存):
首次: 50ms
后续: 5-10ms
```

**与 list_files 对比**:

| 操作 | list_files | file_search |
|------|-----------|-------------|
| 文件扫描 | 20ms | 10ms (缓存) |
| 返回数量 | 100+ | 20 |
| Token 消耗 | 3000 | 300 |
| 准确性 | 低 | 高 |

---

## ✅ 总结

**FileSearch 原理**:
1. 模糊匹配（不需要精确）
2. 相关性评分（多因素）
3. 智能排序（最相关的在前）
4. 自动过滤（排除无关目录）

**实现方式**:
- 简单版: `rapidfuzz` + 基础评分
- 完整版: 多因素权重 + 缓存优化

**效果**:
- 用户友好（不需要记 glob 语法）
- Token 高效（只返回相关结果）
- 速度快（<100ms）

---

**准备好开始实现了吗？** 我会用 rapidfuzz 实现一个简单但强大的版本！
