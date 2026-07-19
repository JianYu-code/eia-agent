# 环评智能审核引擎完善计划（对标 eia-agent.top 审核流程全复刻）

> 目标：修复审核可信度缺陷，复刻参考系统的 K 文件体系、统一队列、分域管线、反馈闭环。
> 决策记录：四期全做按序执行；反馈闭环完整复刻（标记+汇总+注入 prompt）；不引入订阅/配额（内网免登录）。

---

## 现状审核流程的关键不足（分析结论）

### 参考网站分析结论
- **eia-agent.top**：审核流程可 100% 复刻架构 —— 上传→解析→注入K文件→多步骤智能体→报告；K1-K17 知识文件覆盖 7 大类（标准/分类/专项/工程分析/措施/结构/法规）；统一顺序队列防并发打爆 API；验收审核专属 7 阶段（适用性识别→资料完整性→手续与责任主体→验收自查→监测/调查技术→后续验收→不得通过情形）；每条意见可标记误报/漏报/准确/建议调整并有管理员汇总。K 文件具体内容在服务端不可下载，但底层依据均为公开法规，可自建等效体系。
- **ai.eiacloud.com（尚云AI）**：对话式 Agent，审核逻辑全在服务端，仅能确认存在「环保文件AI审核」「环评报告表审核」「监测报告智能审核」三类机器人。不适合本项目形态，不复刻。

### 本项目不足（按严重度）
1. 🔴 **LLM 只看全文前 4000~5000 字符**（<5%），源强/措施/预测等核心章节不在上下文；`chapters` 已切分但从未使用
2. 🔴 **异常静默吞掉**：所有 step `except Exception: pass`，LLM 故障 = 步骤"通过"，结果不可信
3. 🔴 **文件夹审核是坏的**：`extract_text` 对目录路径直接 `open()` 抛异常；验收/应急必须支持资料包
4. 🔴 **无队列无真停止**：BackgroundTasks 并发执行；`/api/stop` 只改状态但 pipeline 不检查
5. 🟠 **确定性检查太浅**：`exact_standard_lookup` 精确索引已建但从未被调用；章节检查是全文字符串匹配；标准正则漏 DB 地方标准；报告类型判定脆弱
6. 🟠 **规则覆盖窄**：仅 8 条，缺专项设置/法规符合性/评价等级逐项/监测有效性/公参/附图；无行业识别，KB 检索词硬编码"锅炉"
7. 🟠 **三域混审**：应急预案触发锅炉重算；验收无 7 阶段、无资料完整性
8. 🟡 **无评级落库/无步骤徽章/无问题定位/无反馈闭环**

---

## 总体目标架构

```
上传(单文件/资料包) → 排队(统一顺序队列) → 解析(多文件提取+章节切分)
→ 识别(报告类型+行业+适用K文件) → 注入(K文件+行业标准KB)
→ 分域管线审核(环评11步/验收7阶段/应急资料包)
→ 综合评级(A/B/C/D) → 报告(HTML/Word/PDF+步骤徽章+问题定位)
→ 反馈闭环(误报/漏报/准确→汇总→反哺prompt)
```

---

## P1 — 审核可信度修复

### 1.1 章节感知上下文路由
- 新建 `backend/app/engine/context.py`：
  - `route_chapters(chapters, patterns, budget=30000)` — 按正则匹配章节标题，拼接相关章节全文，超长按段落截断
  - `build_step_context(text_data, target_patterns)` — 无匹配时回退全文摘要
- 改造全部 steps/*.py 与 `run_llm_check`：签名 `(full_text)` → `(text_data)`，使用路由后上下文

### 1.2 LLM 调用健壮化
- `llm/client.py`：`chat()` 加 timeout=120 + 失败重试 2 次（指数退避）
- 新建 `engine/llm_json.py`：`parse_llm_json(resp)` 剥标记/提取 JSON/尾逗号修复；失败抛 `LLMParseError`
- step 结果三态：`{"status": "pass"|"fail"|"error", "issues": [...]}`；error 写入日志并在报告标"检查失败，需人工复核"

### 1.3 统一顺序队列 + 真停止
- 新建 `engine/queue.py`：单 worker `asyncio.Queue`；start 只置 `queued` 入队；前序完成自动启动下一个
- pipeline 每个 step 前检查 `status == "stopped"` → 中断
- `/api/stop` 对 queued 任务直接移出队列

### 1.4 文件夹/资料包提取
- `extractor.py`：目录路径 → 遍历 DOCX/PDF/MD/TXT 合并提取，输出 `files: [{name, text, chapters}]`
- `classify_package_files(files)` → 批复/监测报告/验收意见/公开证据/预案/风险评估 等分类，供资料完整性检查

---

## P2 — K 文件体系 + 行业适配（对标 K1-K17）

### 2.1 K 文件库（新建 `backend/kfiles/`，依据公开法规编写）
| 文件 | 内容 |
|---|---|
| K01-标准有效性.md | 标准版本判定、废止替代查证方法 |
| K02-分类管理名录.md | 2021 版名录判定要点、敏感区升级规则 |
| K03-专项设置.md | 地下水/土壤/风险/生态/大气专项设置条件 |
| K04-工程分析源强.md | 核算方法优先序、类比 5 条件、非正常工况 |
| K05-产污系数手册.md | 系数法适用与出处要求 |
| K06-污染防治措施.md | 可行技术/BAT、参数完整性、达标论证 |
| K07-报告结构.md | HJ 2.1 报告书/报告表章节要求 |
| K08-法规符合性.md | 产业政策、规划环评、三线一单 |
| K09-评价等级范围.md | 各要素等级判定与评价范围 |
| K10-环境现状监测.md | 布点/频次/时效/引用数据有效性 |
| K11-预测模型.md | AERMOD/ADMS/CALPUFF 适用条件 |
| K12-危废管理.md | 名录归类、暂存处置要求 |
| K13-总量控制.md | 总量指标与区域削减 |
| K14-公众参与.md | 公参程序（报告书适用） |
| K15-附图附件.md | 必备图件清单 |
| K16-验收程序.md | 验收暂行办法主干 + 不得通过情形 |
| K17-应急预案.md | 备案管理办法 + 风险评估要点 |

### 2.2 注入机制
- 新建 `engine/kfiles.py`：`select_kfiles(domain, report_type, industry)` → 加载相关 K 文件全文进 prompt（不走向量检索，保证依据确定性）
- KB 检索词由 `行业+检查主题` 动态生成，替换硬编码"锅炉"查询

### 2.3 行业识别前置步
- pipeline 新增 Step 0「报告识别」：LLM 判定 `{report_type, industry, elements}` → 驱动 K 文件选择、KB 检索词、源强重算启停
- 替代首 2000 字含"报告表"的脆弱判定

### 2.4 规则库扩编
- eia_rules.yaml 8 条 → ~25 条：专项设置、三线一单、总量、监测计划、公众参与、附图附件、各要素评价等级逐项
- 前端 rules.html 展示 K 文件清单（对标参考系统"审核文件"页）

---

## P3 — 三类审核分管线 + 确定性增强

### 3.1 管线按 domain 分离
- `EIA_STEPS`：11 步保留 + 行业适配
- `ACCEPTANCE_STEPS`（复刻参考 7 阶段）：适用性识别→资料完整性→手续与责任主体→验收自查→监测/调查技术→后续验收→不得通过情形（超标/设施未落实/重大变动/数据不实/无证排污等逐条核）
- `EMERGENCY_STEPS`：资料包完整性（预案/风险评估/资源调查/编制说明/发布令/备案附件）+ 现有规则
- step 函数检查 domain，不相关直接 skip

### 3.2 确定性检查升级
- 标准有效性改用 `exact_standard_lookup` 精确判定（现行/废止/替代者）；正则补 `DB\s*\d+`
- 章节检查改为匹配 `chapters` 标题
- 源强重算扩展废水 COD/氨氮（浓度×水量）、VOCs 物料衡算
- coefficient_db 补重点行业系数

---

## P4 — 反馈闭环 + 报告升级

### 4.1 反馈闭环（完整复刻参考系统）
- 新表 `audit_issues`（问题结构化落库：project_id, rule_id, severity, title, finding, evidence, step, feedback, feedback_note）
- API：`POST /api/issues/{id}/feedback`（误报/漏报/准确/建议调整+说明）；`GET /api/admin/quality-feedback-summary`（按规则聚合准确率/误报率）
- 前端：报告页每条问题加反馈按钮；管理页"审核质量"汇总
- 反哺：`run_llm_check` 注入该 rule_id 历史反馈（"此规则历史误报 N 次…请谨慎判定"）

### 4.2 报告升级
- 综合评级落库：`Project.result_summary = {grade, score, p0/p1/p2_count}`；agent 综合审查输出结构化 JSON 单独解析
- 步骤徽章渲染（启用现有死代码样式；pass 绿/fail 红/error 灰）
- 问题定位到章节标题+页码（PDF 提取记录页码）
- Word 导出从结构化 issues 直接生成；PDF 用 HTML 打印方案

### 4.3 审核统计
- `GET /api/admin/audit-stats`：通过率、问题分布、误报率趋势；overview 页展示

---

## 执行顺序与验证

| 期 | 交付物 | 验证 |
|---|---|---|
| P1 | context.py, llm_json.py, queue.py, extractor 目录支持 | 长报告源强章问题可检出；LLM 故障标 error 非 pass；并发任务排队 |
| P2 | kfiles/ ×17, kfiles.py, 行业识别, 规则扩编 | 非锅炉行业不套锅炉逻辑；日志可见"已注入 K03/K04" |
| P3 | 分管线 ×3, 确定性升级 | 验收缺批复→P0；废止标准精确命中替代关系 |
| P4 | 反馈闭环, 报告升级, 统计 | 标记误报→汇总可见→再审 prompt 含历史反馈 |

每期完成跑 `backend/tests/test_engine.py` 并补单测。

**不改动**：LanceDB 入库管线、RAG 问答、LLM Profile 管理、内网免登录设计。

---

## 执行日志

### P1 审核可信度修复（已完成）
- [x] 1.1 `engine/context.py` 章节感知路由（30k 预算，无匹配回退全文）
- [x] 1.2 `engine/llm_json.py` JSON 解析修复 + `llm/client.py` timeout=180 重试×3
- [x] 1.3 `engine/queue.py` 顺序队列（单 worker）+ pipeline 逐步停止检查 + `/api/stop` 真取消
- [x] 1.4 `extractor.py` 资料包目录提取 + `classify_file` / `package_completeness` 文件分类
- [x] 步骤三态（pass/fail/error）：异常不再静默，报告红色横幅 + 日志明确"检查失败需人工复核"
- [x] 步骤徽章渲染（启用原死代码样式）

### P2 K 文件体系 + 行业适配（已完成）
- [x] 2.1 `backend/kfiles/` K01-K17 知识文件（依据公开法规编写，覆盖参考系统 7 大类）
- [x] 2.2 `engine/kfiles.py`：按域/报告类型/行业选择注入；`identify_report` LLM+关键词双通道
- [x] 2.3 eia_rules.yaml 8→23 条（专项设置/三线一单/总量/公参/附图/监测/预测/危废/自洽等），全部绑定 K 文件+目标章节
- [x] `/api/admin/kfiles`、`/api/admin/rules` 动态接口 + rules.html 重写展示

### P3 三类审核分管线 + 确定性增强（已完成）
- [x] 3.1 环评 11 步 / 验收 7 阶段（复刻参考流程）/ 应急 5 步 分管线
- [x] 验收资料包检查（K16 六件套，缺批复→P0）、应急备案包检查（K17 七件套）
- [x] 标准有效性：`exact_standard_lookup` 精确判定（废止→P0+替代关系），正则补 DB 地标，finditer 修复捕获组 bug
- [x] 章节检查改为章节标题匹配（结构退化时回退全文）
- [x] 源强重算扩展废水线（Q×C 反算 COD/氨氮）；系数库补水泥/钢铁/电镀/养殖

### P4 反馈闭环 + 报告升级（已完成）
- [x] 4.1 `audit_issues` 表结构化落库；`POST /api/issues/{id}/feedback`（准确/误报/调整+说明）
- [x] 报告页每条问题反馈按钮；`/api/admin/quality-feedback-summary` 聚合准确率/误报率；管理日志页展示
- [x] 反馈反哺 prompt：`run_llm_check` 注入该规则历史误报统计与典型误报情形
- [x] 4.2 综合评级 A/B/C/D + 总结 + Top3 落库 `Project.result_summary`；问题定位到章节；报告含打印/另存 PDF
- [x] Word 导出改从结构化问题生成（旧报告回退 HTML 刮取）
- [x] 4.3 `/api/admin/audit-stats` + overview 页审核质量统计块（通过率/误报率/类别分布）

### 验证
- [x] 单测 13 项全过（章节路由/JSON修复/资料包/K文件/标准精确判定/章节标题匹配等）
- [x] 端到端冒烟（模拟LLM）：环评管线完成，评级C落库，报告含徽章/反馈按钮
- [x] 队列顺序执行 + 取消排队验证
- [x] 验收资料包冒烟：缺批复→P0，7阶段步骤完整

---

## K-Hub 知识库 Obsidian 化（已完成）

> 决策：vault 仅目录兼容（不依赖 Obsidian 软件）；PDF 双通道并存（extractor + MinerU 汇入）；只要手动按钮（不做每日定时）。

### Part 0 — 故障容错（修复 WinError 10061 类崩溃）
- [x] `retriever`：lancedb 缺失/Ollama 断连 → `search_knowledge` 返回空并警告一次，不再致命
- [x] `pipeline`：standards_kb 检索 try/except 降级，日志明确提示"标准核查仅用精确索引"
- [x] `GET /api/knowledge/health`：Ollama 连通性 + 索引行数健康灯

### Part 1 — K-Hub 运营层
- [x] 配置：`KNOWLEDGE_INBOX_DIR` / `KNOWLEDGE_VAULT_DIR`，页面可改（存 `kv_settings` 表）
- [x] 模型：`KnowledgeFile`（sha256 唯一索引判重）+ `KVSetting`，自动建表
- [x] `organizer.py`：扫描三态(new/changed/duplicate) → extractor 提取 → LLM 摘要 → vault MD（frontmatter 来源/哈希/时间/类别/摘要）→ 类别自动归档；MinerU 通道并入保留分类路径；>50MB 跳过
- [x] `reindex.py`：vault 全量重建（清 vault/ 前缀旧行→分块→分批向量化→入库），进度写 kv_settings；`delete_vault_file` 删除联动
- [x] API：hub-settings / organize / reindex / organize-status / files / files/{id}（DELETE）
- [x] 前端 `/app/knowledge-hub`：目录设置 + 立即整理(±MinerU) + 重建索引 + 进度轮询 + 健康灯 + 文件列表/删除；导航入口
- [x] RAG sources 带 vault_path

### 验证
- [x] 单测 15 项全过（新增：重建索引分块、K-Hub 整理器三态/判重/变更识别）
- [x] 重建索引假库测试：2 文件→6 分块、standard_id 识别、删除联动
- [x] 全路由注册（64 条）；app 在无 lancedb 环境也可启动（降级）

---

## 管理员口令访问控制（已完成）

> 决策：方案 A 管理员口令；仅保护大模型API/知识库管理/知识入库三项；口令自动生成+控制台查看。
> UX：无口令用户侧栏三项默认灰色禁用+🔒，悬停提示"您当前无权限访问"，点击不跳转。

- [x] `api/deps.py`：token 三级来源（env ADMIN_TOKEN → admin_token.txt → 自动生成写盘），`hmac.compare_digest` 校验
- [x] 守卫挂载：admin 5 条（llm-config GET/POST、test、cache-stats、cache-clear）+ token 修改；knowledge 11 条（sync/detect-obsolete/remove/health/hub-settings×2/organize/reindex/organize-status/files×2）
- [x] `GET /api/admin/verify`（探测）、`POST /api/admin/token`（改口令，受保护）
- [x] 启动控制台打印 `🔑 管理员口令`
- [x] `base.html`：api() 自动带头 + 401 弹口令框重试；导航默认锁定→静默 verify 解锁；`requireAdminPage()` 直访门禁
- [x] 设置页"修改管理员口令"卡片（改后同步本机 localStorage 并解锁导航）
- [x] 开放接口（审核/报告/问答/检索/规则/概览/日志/反馈）不受影响

### 验证
- [x] TestClient：无/错口令 401、对口令 200、verify、改口令生效、开放接口 200
- [x] 15 项单测回归通过；模板语法检查通过

---

## 硬伤三项修复（已完成）

### 1. 报告 XSS 修复
- [x] `_generate_report` / `_step_badges_html` 全部 21 处 LLM/用户可控插值统一 `html.escape()`
- [x] `<script>alert(1)</script>` 注入测试：输出中无原始脚本

### 2. 审核并发提速
- [x] `llm/client.py` 全局 `Semaphore(3)`（`LLM_CONCURRENCY` 可调）包住 chat/chat_vision
- [x] `run_step` 步骤内规则+专用函数 `asyncio.gather` 并发；单规则失败不拖垮整步（全部失败才标 error，部分失败日志提示）
- [x] e2e：27 次 LLM 调用并发峰值 ≤3

### 3. 逐章 map-reduce 审核
- [x] `context.segment_chapter`（15k+overlap 切段）、`pick_target_chapters`（10 章/200k 上限）
- [x] `rules_engine.run_llm_check_per_chapter`：逐章独立判定 → (rule_id,归一化标题) 去重保高严重级；无匹配章节回退单调用
- [x] YAML `per_chapter: true`：R-SRC-001/002、R-MSR-001、R-PRD-001、R-MON-001、R-TOT-001（源强/措施/预测/监测/总量重灾区）
- [x] 测试：3 章调用、去重正确、并发峰值 ≤3

---

## 生成 × 审核双闭环（已完成）

> 决策：自审核用完整 11 步管线；交付 MD+自审报告+DOCX；加可研上传自动填表。

- [x] `audit_runner.py`：审核核心抽取（识别→11步→综审→去重→章节定位），审核管线与生成自审核双路复用，审核回归通过
- [x] `report_generator.py` 重写：章节×K文件映射注入、行业适配（报告书9章/报告表6章）、避坑警示、9章并发生成、`render_docx` 结构化导出
- [x] `gen_repair.py`：P0/P1 按章节聚合 → 并发修复（K文件注入）→ ≤2轮 + 修复日志
- [x] `generate.py` 新流程：生成(10-50%)→落盘→自审核(52-85%)→修复(85-95%)→三件套交付；自审核问题落库汇入反馈闭环；评级落 `result_summary`
- [x] `POST /api/generate/parse-proposal`：可研 DOCX/PDF → LLM 提取 17 字段自动填表
- [x] `generate.html`：可研上传卡、阶段化进度+实时日志、完成后评级徽章+三下载（MD/DOCX/自审核报告）
- [x] 验证：K文件注入断言、修复回路单测、e2e 双闭环（9章并发生成→自审核→评级B→三件套）、审核管线 refactor 回归、68 条路由

---

## 视觉审核升级 + 截断修复（已完成）

> 决策：视觉模型 glm-4.6v-flash（vision_review 兜底生效）；全部图件逐张查。

### A. 截断打捞
- [x] `llm_json.parse_llm_json`：JSON 未闭合时截到最后完整键值对/元素补齐解析（修掉"图文一致性 JSON 解析失败"真实故障）
- [x] `max_tokens=4096`（`LLM_MAX_TOKENS` 可调），文本/视觉调用统一

### B/C. 图件提取 v2 + 分型视觉检查
- [x] PDF：图注页检测渲染（矢量流程图/布置图全覆盖）；扫描件均匀抽样回退
- [x] DOCX：内嵌图 >10KB 全取；`VISION_MAX_FIGURES` 默认 20 上限
- [x] 图文配对：图注 + 上下文段落作为判定依据
- [x] 分型检查：图型分类（工艺/布置/布点/敏感目标/数据图表/地理位置）→ 类型专属检查 → 结构化 JSON 裁决（弃用关键词嗅探）
- [x] 容错：单图失败记 P2 提示不拖垮步骤；全部失败才标 error；无视觉模型自动回退纯文本比对
- [x] issue 带图注位置（chapter 字段），rule_id R-FIG-VIS-001

### 验证
- [x] 打捞 7 用例、图注识别、PDF 图注页渲染、扫描件抽样
- [x] 视觉分型/全失败 error/部分失败容错/文本降级 4 场景
- [x] 审核 e2e（含图 PDF）：VLM 实际被调用，管线 completed
- [x] 15 项单测回归
