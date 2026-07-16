# AI 环评智能审核平台 — 实施计划

## 概述

参考 **eia-agent.top**（环评智能审核系统）和 **环评五四三**（eia543.com）知识体系，
构建一个 AI 驱动的环评智能审核平台，具备以下核心能力：

- 环评报告智能审核（上传 → 自动分析 → P0/P1/P2 问题输出 → HTML 报告）
- 知识库 RAG 问答（基于 3044 份环保标准/导则/规范文档）
- 文件上传与管理（项目队列、状态追踪、实时日志）
- 智能报告生成（审核结果结构化输出）
- 审核规则引擎（可配置的 YAML 规则集）

---

## 技术栈

| 层 | 选择 | 版本/说明 |
|---|------|----------|
| 后端框架 | FastAPI | 异步、自带 OpenAPI 文档 |
| 认证 | 无（内网部署，15人团队，免登录） | — |
| ORM | SQLAlchemy 2.0 | async session |
| 关系数据库 | SQLite | aiosqlite 驱动 |
| 向量数据库 | LanceDB | 嵌入式列式存储，预构建 wheel 无需编译器 |
| Embedding | bge-large-zh-v1.5 | 1024 维，sentence-transformers |
| LLM SDK | langchain-openai | OpenAI 兼容接口 |
| PDF 解析 | PyMuPDF (fitz) | 文本 + 表格 |
| DOCX 解析 | python-docx | 段落 + 表格 |
| 报告渲染 | Jinja2 | HTML 报告模板 |
| 实时推送 | SSE (sse-starlette) | 审核进度 + 日志 |
| 前端 | Jinja2 + Tailwind CSS CDN | 零构建工具 |
| 规则配置 | YAML | 可编辑的审核规则 |
| 任务队列 | FastAPI BackgroundTasks | 异步审核管道 |

---

## 数据源

| 来源 | 位置 | 文件数 | 格式 | 总大小 | 用途 |
|------|------|--------|------|--------|------|
| MinerU 处理后标准库 | `C:\Users\haobo\Desktop\output\a` | 3,044 | Markdown + images | 20 GB | 知识库核心 |
| 环保小智报告 | `C:\Users\haobo\环保小智_文档下载\报告` | ~2,310 | PDF/DOCX | — | 真实案例 + 测试 |
| 环保小智文档 | `C:\Users\haobo\环保小智_文档下载\文档` | ~10,224 | PDF/DOCX/DOC | — | 国家规范 + 地方标准 |

MinerU 输出结构：
```
output/a/{分类}/{子类}/{文档名}/hybrid_auto/{文档名}.md
                                                /images/*.jpg
```

12 个分类：
1. 01-基础性标准规范（409 MD）
2. 02-污染要素管控（479 MD）
3. 03-污染源强及温室气体核算（338 MD）
4. 04-生态保护与修复（88 MD）
5. 05-行业污染源防治（435 MD）
6. 06-工程与施工管控（156 MD）
7. 07-地方标准与政策（82 MD）
8. 08-风险防控与应急（191 MD）
9. 09-温室气体与碳交易（98 MD）
10. 10-环境与健康（140 MD）
11. 11-监管与审批（399 MD）
12. 12.文献资料（229 MD）

---

## 项目目录结构

```
eia-agent/
├── PLAN.md                          # 本文档

├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI 入口 + 挂载路由
│   │   ├── config.py                # 全局配置
│   │   ├── database.py              # SQLite 连接（向量库在 retriever 中独立管理）
│   │
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── project.py           # Project, LLMProfile, FileIndex 模型
│   │
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── projects.py          # 上传/队列/审核/报告下载
│   │   │   ├── knowledge.py         # 知识检索/RAG 问答/统计
│   │   │   └── admin.py             # LLM配置/系统日志/项目清除
│   │
│   │   ├── engine/                  # ★ 审核引擎
│   │   │   ├── __init__.py
│   │   │   ├── pipeline.py          # 审核管道编排器
│   │   │   ├── extractor.py         # PDF/DOCX/MD 文本提取
│   │   │   ├── steps/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── structure.py     # ① 章节完整性
│   │   │   │   ├── standards.py     # ② 标准引用有效性
│   │   │   │   ├── source.py        # ③ 源强核算核查 ★
│   │   │   │   ├── classification.py# ④ 分类管理判定
│   │   │   │   └── measures.py      # ⑤ 措施可行性
│   │   │   └── grader.py            # P0/P1/P2 分级器
│   │
│   │   ├── knowledge/
│   │   │   ├── __init__.py
│   │   │   ├── loader.py            # MD 分块 → 向量化
│   │   │   ├── retriever.py         # LanceDB 向量检索
│   │   │   └── rag.py               # RAG 问答
│   │
│   │   ├── llm/
│   │   │   ├── __init__.py
│   │   │   └── client.py            # OpenAI 兼容客户端
│   │
│   │   └── report/
│   │       ├── __init__.py
│   │       ├── generator.py         # HTML 报告渲染
│   │       └── templates/
│   │           └── report.html      # Jinja2 报告模板
│   │
│   ├── prompts/
│   │   ├── audit_system.txt         # 审核系统 prompt
│   │   └── rag_system.txt           # RAG 系统 prompt
│   │
│   ├── rules/
│   │   ├── eia_rules.yaml           # 环评报告规则
│   │   ├── acceptance_rules.yaml    # 竣工验收规则
│   │   └── emergency_rules.yaml     # 应急预案规则
│   │
│   └── requirements.txt
│
├── frontend/
│   ├── templates/
│   │   ├── base.html                # 基础框架（side nav + header）
│   │   ├── home.html                # 首页（自动跳转到概览）
│   │   ├── overview.html            # 首页概览
│   │   ├── audit.html               # 审核工单
│   │   ├── realtime.html            # 实时审核
│   │   ├── knowledge.html           # 知识问答
│   │   ├── settings.html            # 大模型 API 配置
│   │   └── admin/
│   │       ├── users.html           # 用户管理
│   │       ├── files.html           # K 文件管理
│   │       └── logs.html            # 系统日志
│   └── static/
│       └── css/
│           └── style.css            # 全局样式 + 暗色主题
│
├── scripts/
│   └── ingest_mineru.py             # MinerU MD → LanceDB (支持 full/sync/remove)
│
└── uploads/                          # 上传文件存储目录
```

---

## 数据库模型设计

> **注：** 系统为内网部署，已移除认证和用户管理模块，无需登录。

### Project
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| name | String(500) | 项目名称 |
| filename | String(500) | 原始文件名 |
| file_path | String(1000) | 服务器文件路径 |
| file_size | Integer | 文件大小(bytes) |
| audit_domain | String(50) | eia/acceptance/emergency |
| status | String(20) | uploaded/queued/running/completed/failed/stopped |
| progress | Float | 0-100 |
| step | String(200) | 当前步骤描述 |
| issues | JSON | {P0: [...], P1: [...], P2: [...]} |
| logs | JSON | [{time, message, type}] |
| report_path | String(1000) | 审核报告 HTML 路径 |
| result_summary | JSON | {p0_count, p1_count, p2_count, score} |
| deleted_by_user | Boolean | 软删除标记 |
| created_at | DateTime | |
| updated_at | DateTime | |

### LLMProfile
| 字段 | 类型 | 说明 |
|------|------|------|
| id | String(100) | 主键（自定义 ID） |
| name | String(200) | 配置名称 |
| base_url | String(500) | API 地址 |
| model | String(200) | 模型名 |
| api_key_hash | String(500) | 加密存储的 API Key |
| purpose | String(50) | audit / vision_review / other |
| pool_enabled | Boolean | 并发池开关 |
| max_retries | Integer | 最大重试 |
| extra_body | JSON | 额外请求参数 |
| active | Boolean | 当前启用的问答模型 |
| vision_active | Boolean | 当前启用的视觉模型 |
| created_at | DateTime | |
| updated_at | DateTime | |

---

## API 设计

### 项目审核模块 `/api/projects`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/upload | 上传单文件（DOCX/PDF/TXT/MD） |
| POST | /api/upload-folder | 上传文件夹（MinerU 输出目录） |
| GET | /api/projects | 获取项目列表（按时间倒序） |
| POST | /api/projects/{id}/start | 启动审核（异步执行） |
| GET | /api/projects/{id}/report/view | 查看 HTML 审核报告 |
| GET | /api/projects/{id}/report/download | 下载报告 |
| DELETE | /api/projects/{id} | 删除项目 |
| POST | /api/stop | 暂停审核任务 |
| GET | /api/projects/{id}/stream | SSE 实时审核进度 |

### 知识库模块 `/api/knowledge`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/knowledge/search?q={query} | 知识库检索（返回来源片段） |
| POST | /api/knowledge/ask | RAG 问答（含对话历史） |
| GET | /api/knowledge/stats | 知识库统计 |
| POST | /api/knowledge/ingest | 重新入库 |

### 管理模块 `/api/admin`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | /api/admin/llm-config | LLM 配置管理 |
| POST | /api/admin/llm-config/test | 测试 LLM 连通性 |
| GET | /api/admin/logs | 系统操作日志 |
| POST | /api/admin/projects/{id}/purge | 清除项目记录 |

---

## 审核引擎设计（核心）

### 审核管道 Pipeline

```
                        ┌──────────────────────┐
                        │  POST /projects/{id}/start
                        └──────────┬───────────┘
                        └──────────┬───────────┘
                                   │ 异步 BackgroundTask
        ┌──────────────────────────┼──────────────────────────┐
        ▼                          ▼                          ▼
  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
  │ ①提取    │ → │ ②结构    │ → │ ③标准    │ → │ ④源强    │ → │ ⑤措施    │
  │ 文本     │   │ 完整性   │   │ 有效性   │   │ 核算     │   │ 可行性   │
  │ 0→15%   │   │ 15→25%  │   │ 25→40%  │   │ 40→65%  │   │ 65→80%  │
  └──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
                                                                    │
                                              ┌─────────────────────┘
                                              ▼
                                        ┌──────────┐   ┌──────────┐
                                        │ ⑥等级    │ → │ ⑦报告    │
                                        │ 判定     │   │ 生成     │
                                        │ 80→90%  │   │ 90→100% │
                                        └──────────┘   └──────────┘
```

### Step 1: 文本提取（extractor.py）

```
输入: 上传文件路径
处理:
  - PDF → PyMuPDF (fitz) 提取文本 + 表格
  - DOCX → python-docx 提取段落
  - MD/TXT → 直接读取
输出: {
  full_text: str,          # 全文
  chapters: [{             # 章节列表
    title: str,
    level: int,            # 标题层级 1-4
    content: str
  }],
  tables: [[str]]          # 提取的表格
}
progress: 15%
```

### Step 2: 章节结构完整性（structure.py）

```
规则: 根据 HJ 2.1-2016 总纲要求，
      环评报告书必须包含以下章节：

检查项:
  □ 前言
  □ 总则（编制依据、评价因子、评价标准、评价等级）
  □ 建设项目工程分析
  □ 环境现状调查与评价
  □ 环境影响预测与评价
  □ 环境保护措施及其可行性论证
  □ 环境影响经济损益分析
  □ 环境管理与监测计划
  □ 环境影响评价结论

方式: 关键词/正则匹配（不需要 LLM）
判定: 缺失必填章节 → P0
progress: 25%
```

### Step 3: 标准引用有效性（standards.py）

```
规则: 报告中引用的环境标准必须为现行有效版本

处理流程:
  1. 正则提取报告中所有标准编号: (GB|GB/T|HJ|HJ/T)\s*[\d.\-]+
  2. 对每个标准编号，在知识库中检索替代/更新情况
  3. 判断是否已废止、是否有新版替代

例子:
  报告引用 "GB 16297-1996" 
  → 检索知识库发现该标准已被 "GB 37822-2019, GB 39726-2020, GB 41616-2022" 替代
  → P0: 引用已废止标准，请更新引用依据

progress: 40%
```

### Step 4: 源强核算核查 ★（source.py）

```
这是最核心的审核步骤，也是 60% 报告出问题的区域。

处理流程:
  1. 识别报告中的行业类型
  2. 在知识库中检索该行业的源强核算技术指南（如 HJ 996.1）
  3. 从指南中提取：应识别的污染因子、核算方法优先次序、类比条件
  4. 将报告内容 + 指南内容 + 问题发给 LLM 判断

LLM 检查项:
  □ 评价因子是否遗漏（核对指南中的必需因子）
  □ 核算方法次序是否符合指南
    - 优先法: 物料衡算法
    - 次选: 类比法（需满足 5 个类比条件）
    - 再次: 产污系数法
  □ 类比法使用时，是否满足所有类比条件：
    1. 原料/燃料类别相同，成分差异 ≤ 10%
    2. 辅料类型相同
    3. 生产工艺相同
    4. 产品类型相同
    5. 规模差异 ≤ 30%
  □ 是否包含正常排放 + 非正常排放两种情况
  □ 源强取值是否有出处（类比哪个项目？哪个实测数据？）

知识库查询:
  - 检索 Collection "eia_standards"
  - 查询: {行业名} + "源强核算技术指南"
  - 查询: "产污系数手册" + {行业名}

progress: 65%
```

### Step 5: 污染防治措施可行性（measures.py）

```
处理流程:
  1. 识别报告中描述的污染治理措施
  2. 检索可行技术指南 + 最佳可行技术（BAT）
  3. LLM 判断措施是否可行

LLM 检查项:
  □ 措施是否为"技术可行"而非"形式描述"
    例: "采用活性炭吸附处理有机废气" 
    → 缺少: 活性炭类型、更换频率、处理效率、设计参数
  □ 处理效率是否有依据
  □ 是否考虑了最不利工况
  □ 治理后排放浓度是否能满足排放标准限值

progress: 80%
```

### Step 6: 分类管理等级判定（classification.py）

```
处理流程:
  1. 识别项目的行业类别 + 规模 + 环境敏感程度
  2. 检索《建设项目环境影响评价分类管理名录（2021年版）》
  3. LLM 判断环评等级是否正确

LLM 检查项:
  □ 环评等级是否正确: 报告书 / 报告表 / 登记表
  □ 审批权限是否正确: 部级 / 省级 / 市级

progress: 90%
```

### Step 7: 报告生成（generator.py）

```
输入: 所有审查结果

处理:
  1. 汇总所有 issues
  2. 按 P0 → P1 → P2 排序
  3. 每个 issue 包含: 
     - rule_id, severity, category
     - finding（问题描述）
     - evidence（报告中原文引用）
     - law_ref（法规依据）
     - suggestion（修改建议）
  4. 渲染 Jinja2 模板 → HTML

输出: 结构化审核报告 HTML
progress: 100%
status: completed
```

### 问题分级标准

| 级别 | 标签 | 含义 | 典型场景 | 颜色 |
|------|------|------|---------|------|
| P0 | 严重 | 否决性，报告必须整改 | 源强核算无依据、评价等级错误、引用作废标准 | 🔴 #ef4444 |
| P1 | 一般 | 缺陷，需补充说明 | 核算参数偏低、监测布点不足、措施描述不详 | 🟠 #f59e0b |
| P2 | 建议 | 优化建议 | 用词不严谨、引用非最新文献、可更精确估算 | 🔵 #3b82f6 |

> **注：** 内网部署无需配额管理，审核无次数限制。

---

## RAG 知识问答设计

### 知识库结构

```
LanceDB Table: "eia_standards"

Schema:
  id           pa.string()
  vector       pa.list_(pa.float32(), 1024)    # bge-large-zh 向量
  text         pa.string()                      # 分块文本
  title        pa.string()                      # 文档标题 (如 "HJ 2.1-2016")
  heading      pa.string()                      # 章节标题 (如 "1 适用范围")
  category     pa.string()                      # 分类路径
  standard_id  pa.string()                      # 标准编号 (如 "HJ 2.1-2016")
  source       pa.string()                      # 源文件路径

分块策略:
  - 按 ## 和 ### 标题分割
  - chunk_size: ~800 chars
  - chunk_overlap: ~100 chars
  - 保留表格 markdown 结构
```

### 检索流程

```
用户问题 → tokenize → embedding(bge-large-zh-v1.5) 
  → LanceDB.table.search(query_vec).limit(5).to_list()
  → 返回: [{id, title, heading, category, excerpt, _distance}]
```

### RAG 问答流程

```
POST /api/knowledge/ask
  body: {
    question: "废活性炭属于什么危废代码？",
    history: [{role, content}, ...]  # 最近 8 轮
  }
  
  → 检索 top_k=5 相关文档
  → 拼装 prompt:
    system: "你是环评审核助手，基于提供的标准文档回答问题..."
    context: [retrieved chunks]
    question: user_question
  → LLM 生成回答
  → 返回: {
    answer: "根据《国家危险废物名录》...",
    sources: [{title, file_path, excerpt}],
    model: "glm-4-flash",
    mode: "rag"
  }
```

---

## 大模型配置系统

支持多套 LLM Profile 并存，可随时切换启用的模型。

```json
{
  "id": "zhipu-glm-4-flash",
  "name": "智谱 GLM-4-Flash",
  "base_url": "https://open.bigmodel.cn/api/paas/v4",
  "model": "glm-4-flash",
  "purpose": "audit",
  "pool_enabled": true,
  "max_retries": 3,
  "active": true
}
```

---

## 前端设计

### 审美方向

| 维度 | 方案 |
|------|------|
| 用途 | 环评审核专业工作台，数据密集型仪表盘 |
| 基调 | 严谨克制的工业技术风 — 暗色背景 + 冷色调强调 |
| 色彩 | 深蓝黑底 (#0f1923) + 白面板 + 信号色 P0/P1/P2 |
| 字体 | 思源黑体 / 苹方等宽展示标准编号和日志 |
| 空间 | 左侧固定 nav + 右侧流式内容，审核日志区全宽占底 |
| 动效 | 进度条动画、日志逐行显示、卡片 hover 微提升 |
| 记忆点 | 审核进度大头环 + P0/P1/P2 三色故障灯 |

### x
### 颜色系统

```
--bg-base:      #0f1923   深蓝黑底
--panel:        #1a2733   面板
--text:         #dde6ed   正文
--accent:       #4fc3f7   信息蓝
--p0:           #ef4444   严重 — 红
--p1:           #f59e0b   一般 — 琥珀
--p2:           #3b82f6   建议 — 蓝
--success:      #10b981   通过 — 绿
--muted:        #64748b   辅助 — 灰
--border:       #2d3d4f   边框
```

### 页面清单

| 页面 | 模板文件 | 主要内容 |
|------|---------|---------|
| 登录/注册 | home.html | 双模式切换、邮箱验证码 |
| 首页概览 | overview.html | 统计卡片、项目列表、趋势图、快捷入口 |
| 审核工单 | audit.html | 审核类型选择、拖拽上传、项目列表 |
| 实时审核 | realtime.html | 队列 + 进度大头环 + 日志终端 |
| 知识问答 | knowledge.html | 对话 + 来源检索引擎 |
| 大模型设置 | settings.html | Profile 列表 + 编辑表单 |
| 知识库管理 | admin/files.html | 统计 + 增量同步 + 废止文档列表 |

---
| 系统日志 | admin/logs.html | 操作日志分类查看 |

### 功能布局（参照原版但不照抄样式）

```
┌──────────────────────────────────────────────────┐
│  Header: logo + 用户信息 + 通知 + 退出            │
├──────────┬───────────────────────────────────────┤
│ Sidebar  │  Page Content                          │
│          │                                        │
│ 首页概览  │  ┌──────────────────────────────────┐ │
│ 实时审核  │  │  页面内容区                        │ │
│ 审核工单  │  │  (按当前页面渲染不同内容)           │ │
│ 审核规则  │  │                                    │ │
│ 知识问答  │  └──────────────────────────────────┘ │
│ 环评日报  │                                        │
│ 招标信息  │                                        │
│ 标准查询  │                                        │
│ ──────── │                                        │
│ 审核文件* │                                        │
│ 大模型API*│                                        │
│ 用户管理* │                                        │
│ 系统日志* │                                        │
├──────────┴───────────────────────────────────────┤
│  * 仅管理员可见                                    │
└──────────────────────────────────────────────────┘
```

---

## 执行步骤

### Step 1: 项目初始化
- [ ] 创建项目目录结构
- [ ] 初始化 Python 虚拟环境
- [x] 编写 requirements.txt 并安装依赖
- [x] 创建 config.py（数据库路径、JWT secret、LLM 配置等）
- [x] 创建 database.py（SQLite 连接 + async session）

### Step 2: 后端骨架
- [x] 编写 User 和 Project 数据模型（SQLAlchemy）
- [x] 编写 JWT 认证工具函数
- [x] 实现 auth API（login, register, logout, me）
- [x] 创建 FastAPI main.py 入口

### Step 3: 知识库管线
- [x] 编写 ingest_mineru.py 脚本（MD → 分块 → embedding → LanceDB）
- [x] 实现 knowledge API（search, ask, stats）
- [x] 编写 RAG prompt 模板
- [x] 实现 LLM 客户端（langchain-openai）

### Step 4: 文件上传与项目队列
- [x] 实现文件上传 API（upload, upload-folder）
- [x] 实现项目列表 API
- [x] 实现项目状态管理（队列、软删除）
- [x] 实现配额检查

### Step 5: 审核引擎
- [x] 编写 extractor.py（多格式文本提取）
- [x] 编写 YAML 审核规则配置
- [x] 实现 pipeline 编排器（5步检查 + 报告生成）
- [x] 实现 SSE 进度推送
- [x] 编写报告生成器 + HTML 模板

### Step 6: 管理功能
- [x] 实现 LLM 配置 API（CRUD + test）
- [x] 实现用户管理 API
- [x] 实现系统日志存储和查询

### Step 7: 前端页面
- [x] 使用 frontend-design skill 设计暗色工业技术风 UI
- [x] 实现 base.html 框架（侧边栏 + 顶栏）
- [x] 实现 8 个页面模板（home/overview/audit/realtime/knowledge/settings + admin×3）
- [x] 编写全局 CSS 样式（浅色 teal 主题，复刻原平台）

---

## 知识库增量更新

### 设计理念

废止的标准不删除，保留在知识库中并标记 `deprecated: true`，供历史报告对比参考。AI 审核时可以识别新旧标准的差异。

### 文件追踪

每条 chunk 记录 `source`（源文件路径）和 `file_mtime`（入库时修改时间），通过对比文件系统实现三态识别：

| 变化 | 操作 |
|------|------|
| 新增文件 | 分块 → 向量化 → 入库 (`deprecated: false`) |
| 文件更新（mtime 变新） | 旧版标记 `deprecated: true` → 新版入库 |
| 文件从磁盘消失 | 标记 `deprecated: true`，不删除 |

### 命令行

```bash
python scripts/ingest_mineru.py full              # 全量重建
python scripts/ingest_mineru.py sync              # 增量同步
python scripts/ingest_mineru.py detect-obsolete   # 检测废止/替代关系
python scripts/ingest_mineru.py remove --sources /path/...  # 删除
```

### API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/knowledge/sync | 增量同步（后台任务） |
| POST | /api/knowledge/detect-obsolete | 扫描废止/替代关系并自动打标 |
| GET | /api/knowledge/outdated | 列出所有废止文档 |
| POST | /api/knowledge/remove | 按 source 路径删除 |

---

## 启动方式

```bash
# 1. 安装依赖
cd eia-agent
pip install -r backend/requirements.txt

# 2. 知识入库（首次全量）
python scripts/ingest_mineru.py full

# 2.5 环保小智文件索引（一次性，~12,000 个 PDF/DOCX）
python scripts/scan_xiaozhi.py full              # 全量扫描建索引
python scripts/scan_xiaozhi.py detect           # 深度废止检测（读PDF内容）

# 3. 启动服务
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 日常维护: 增量同步（只处理变化文件，秒级完成）
python scripts/ingest_mineru.py sync
```

# 4. 访问
http://localhost:8000
```
