# 恒新环保智能系统

AI 驱动的环评智能审核平台，基于 3,000+ 环保标准/导则/规范文档构建知识库。

## 功能

- **环评报告智能审核**：上传 DOCX/PDF → 自动分析 → P0/P1/P2 问题输出 → HTML 报告
- **知识库 RAG 问答**：基于环境标准库的语义检索 + 大模型回答
- **文件检索**：12,000+ 环保标准/报告文件按名称搜索并在线查阅
- **审核规则引擎**：可配置的 YAML 规则集，支持环评/验收/应急预案三种类型

## 环境要求

- Python 3.12+
- [Ollama](https://ollama.com/)（本地运行向量模型）
- LLM API Key（[智谱](https://open.bigmodel.cn/) / [通义千问](https://dashscope.aliyun.com/) / [DeepSeek](https://platform.deepseek.com/) 任选其一）

## 快速启动

```powershell
# 1. 克隆项目
git clone https://github.com/yourname/eia-agent.git
cd eia-agent

# 2. 一键安装
setup.bat

# 3. 拉取向量模型（首次）
ollama pull bge-m3

# 4. 准备数据
# MinerU将文档文件处理后的 MD 放到 C:\Users\xxx\Desktop\output\a\
# 将环保小智文件放到 C:\Users\xxx\环保小智_文档下载\
# 或修改 backend/app/config.py 中的路径

# 5. 知识入库
venv\Scripts\python scripts\ingest_mineru.py full
venv\Scripts\python scripts\scan_xiaozhi.py full

# 6. 启动
venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
cd backend
..\venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 8000

# 7. 浏览器打开
# http://localhost:8000 → 自动跳转首页
# 在 /app/settings 配置 LLM API Key
```

## 命令参考

| 命令 | 说明 |
|------|------|
| `python scripts\ingest_mineru.py full` | 全量入库环评五四三（首次） |
| `python scripts\ingest_mineru.py sync` | 增量同步（日常使用） |
| `python scripts\ingest_mineru.py detect-obsolete` | 检测废止/替代关系 |
| `python scripts\scan_xiaozhi.py full` | 扫描环保小智文件索引 |
| `python scripts\scan_xiaozhi.py detect` | 深度废止检测（读PDF内容） |

## 技术栈

| 层 | 选择 |
|---|------|
| 后端 | FastAPI + SQLAlchemy |
| 数据库 | SQLite + LanceDB |
| 向量化 | Ollama bge-m3 |
| 大模型 | langchain-openai（兼容智谱/通义/DeepSeek） |
| 前端 | Jinja2 + Tailwind CSS CDN |
| PDF 解析 | PyMuPDF + python-docx |

## 项目结构

```
eia-agent/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── config.py            # 配置
│   │   ├── api/                 # REST 接口
│   │   ├── engine/              # 审核引擎
│   │   ├── knowledge/           # 知识库（检索/RAG）
│   │   └── llm/                 # 大模型客户端
│   ├── prompts/                 # LLM 提示词
│   ├── rules/                   # 审核规则 YAML
│   └── requirements.txt
├── frontend/
│   ├── templates/               # Jinja2 页面
│   └── static/css/              # 样式
├── scripts/                     # 入库脚本
├── PLAN.md                      # 实施计划
├── .gitignore
├── README.md
└── setup.bat
```
