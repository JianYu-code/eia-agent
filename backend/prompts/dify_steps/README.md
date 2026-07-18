# Dify 审核工作流配置指南

## 文件清单

| 文件 | 用途 | Dify 节点类型 |
|------|------|-------------|
| `00_code_node.py` | 报告文本预处理 | Code |
| `01_compliance.txt` | 符合性检查 | LLM |
| `02_language.txt` | 语言文字+标准引用 | LLM |
| `03_sensitive.txt` | 敏感目标+环境数据 | LLM |
| `04_calculation.txt` | 计算问题检查 | LLM |
| `05_source.txt` | 源强结果校核 | LLM |
| `06_limits.txt` | 排放标准限值 | LLM |
| `07_coefficients.txt` | 产污系数核对 | LLM |
| `08_hazardous.txt` | 危废代码核查 | LLM |
| `09_measures.txt` | 可行技术检查 | LLM |
| `10_figures.txt` | 图文一致性 | LLM |
| `11_consistency.txt` | 内容自洽性 | LLM |
| `99_agent_review.txt` | Agent 综合审查 | LLM |

## 在 Dify 中创建工作流

### 1. 创建 Chatflow 应用

- 首页 → "创建空白应用" → Chatflow
- 名称：`环评智能审核`

### 2. 配置节点（按顺序拖入）

```
Start → Code(00) → LLM(01) → LLM(02) → LLM(03) → LLM(04)
                 → LLM(05) → LLM(06) → LLM(07) → LLM(08)
                 → LLM(09) → LLM(10) → LLM(11)
                 → VariableAggregator(合并) → LLM(99) → End
```

### 3. 每个节点的配置要点

**Start 节点**：
- 添加输入变量 `report_text`（类型 Text，必填）

**Code 节点**：
- 粘贴 `00_code_node.py` 的内容
- 输入变量：`report_text` = `{{#1.report_text#}}`

**每个 LLM 节点**（步骤1-11）：
- 模型：选你已配置的 DeepSeek/GLM-4
- SYSTEM：粘贴对应文件中 `## SYSTEM` 下的内容
- USER：粘贴对应文件中 `## USER` 下的内容
- 上下文：不使用（已经通过 prompt 传入了报告全文）
- 记忆：不使用
- 输出格式：JSON

**Variable Aggregator 节点**：
- 变量1：`{{#3.text#}}`（步骤1结果）
- 变量2：`{{#4.text#}}`（步骤2结果）
- ... (以此类推，11个变量)
- 合并方式：Concat
- 输出类型：Array[Object]

**Agent 审查 LLM 节点**（步骤99）：
- 粘贴 `99_agent_review.txt` 的内容
- USER 中的 `{{#17#}}` 需要改为实际 Aggregator 节点的编号

**End 节点**：
- 输出变量：`issues` = `{{#17#}}`（Aggregator节点）
- 输出变量：`agent_review` = `{{#18.text#}}`（Agent节点）

### 4. 测试

点击右上角"运行"，输入一段报告文本测试。

### 5. 发布

- 点击"发布" → "API 访问"
- 复制 API Key
- 在项目 `backend/app/config.py` 中设置：
  ```python
  AUDIT_ENGINE = "dify"
  DIFY_API_KEY = "app-xxxxxxxxxxxx"
  DIFY_API_URL = "http://localhost:3000/v1"
  ```

### 6. 切换到 Dify 模式

```powershell
set AUDIT_ENGINE=dify
set DIFY_API_KEY=app-xxxxxxxxxxxx
cd C:\Users\haobo\ai_project\eia-agent
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

上传报告审核即可走 Dify 工作流。

### 7. 导出迁移到工位电脑

Dify → 工作流页面 → 导出 DSL → 在工位电脑的 Dify 中导入 → 发布 → 获取新 API Key → 配置。
