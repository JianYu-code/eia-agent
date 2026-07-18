# Dify 审核工作流配置指南（简化版）

## 工作流结构

```
Start → LLM(步骤1) → LLM(步骤2) → ... → LLM(步骤11) → LLM(Agent) → 直接回复
```

**所有 LLM 节点只有一个变量引用：`{{Start.report_text}}`**

## 配置步骤

### 1. 创建 Workflow 应用

Dify 首页 → "创建空白应用" → 选择 **Workflow**（不是 Chatflow）→ 名称：`环评智能审核`

### 2. Start 节点

点开 Start → 添加输入变量：`report_text`（类型 Text，必填）

### 3. 添加 12 个 LLM 节点（步骤1-11 + Agent审查）

每个 LLM 节点配置：

| 配置项 | 值 |
|--------|-----|
| 模型 | DeepSeek V3 / GLM-4（你已配好的） |
| 上下文 | 不使用 |
| 记忆 | 关闭 |
| SYSTEM | 粘贴对应 .txt 文件中 `## SYSTEM` 下的内容 |
| USER | 粘贴对应 .txt 文件中 `## USER` 下的内容 |

**每个 LLM 节点的 USER 里只有一个变量**：
- `{{Start.report_text}}`（用 `/` 键从 Start 节点选择）

步骤1→11 之间用线串联。最后一个 LLM（Agent审查）的输出连到"直接回复"。

### 4. 文件对照

| 文件 | Dify 节点 # |
|------|-----------|
| `01_compliance.txt` | LLM 1（符合性检查） |
| `02_language.txt` | LLM 2（语言文字+标准引用） |
| `03_sensitive.txt` | LLM 3（敏感目标+环境数据） |
| `04_calculation.txt` | LLM 4（计算问题检查） |
| `05_source.txt` | LLM 5（源强结果校核） |
| `06_limits.txt` | LLM 6（排放标准限值） |
| `07_coefficients.txt` | LLM 7（产污系数核对） |
| `08_hazardous.txt` | LLM 8（危废代码核查） |
| `09_measures.txt` | LLM 9（可行技术检查） |
| `10_figures.txt` | LLM 10（图文一致性） |
| `11_consistency.txt` | LLM 11（内容自洽性） |
| `99_agent_review.txt` | LLM 12（Agent 综合审查） |

### 5. 测试

点右上角"运行" → 粘贴一段报告文本 → 查看输出

### 6. 发布

发布 → API 访问 → 复制 API Key
在项目 config.py 中设置：
```python
AUDIT_ENGINE = "dify"
DIFY_API_KEY = "app-xxxxxxxxxxxx"
DIFY_API_URL = "http://localhost:3000/v1"
```
