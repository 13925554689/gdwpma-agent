# GDWPMA-AI 政府债全过程管理智能体

政府债务"借、用、管、还"全过程管理系统：法规库（国家/省/市县三级）→ 项目库建设与申报审核 → 发行审核 → 使用过程监管 → 偿还与资产监管 → 分县市审核报告 → 问题台账 → 整改闭环与二次复核 → 全过程底稿立卷归档。

## 快速启动

```bash
py app.py          # http://127.0.0.1:5210
# 或双击 start-gdwpma.vbs（静默启动+自动开浏览器）
```

## 七页面

| 路由 | 功能 |
|------|------|
| `/` | 总览：分县市分布、P0 预警、底稿流水 |
| `/regulations` | 法规库：三级分类 + FTS5 全文检索 + 条款详情 |
| `/projects` | 项目库：全生命周期台账 + 材料核对矩阵 |
| `/review` | 审核工作台：借/发/用/还四环节规则扫描 |
| `/ledger` | 问题台账：分县市筛选 + 整改状态机 |
| `/training` | 培训辅导：高频问题 TOP + GLM 法规问答 + 案例警示 |
| `/archive` | 底稿归档：一键立卷导出 |

## 架构

- Flask + SQLite(WAL) + FTS5，零外部服务依赖（GLM 问答除外）
- 规则即数据：`rules/{apply,issue,use,repay}.yaml` 共 56 条规则（P0否决/P1限期补正/P2提示），每条带依据条款+证据指向+审计署实证来源
- LLM 客户端 `services/llm.py`：GLM-5.3-flash（open.bigmodel.cn），凭据从 Hermes config.yaml `providers.zai` 读取（env 回退），仅用于法规问答 RAG，审核判定一律走规则引擎
- 全过程底稿：每个管理动作自动写入 `workpapers` 表，立卷时打包归档

## 验证

```bash
py -m pytest tests/ -v      # 6 项单测（独立临时库，与服务互不干扰）
py scripts/e2e.py           # 28 步 HTTP 全流程（服务须运行中；含登录/PDF抽取/docx/AI点评）
```

## 扩展能力（P1+）

- **单机登录**：admin / gdwpma@2026（PBKDF2 存储；全部页面与 API 需登录）
- **材料上传**：项目详情页上传 PDF/txt/md 自动抽取全文入库（pymupdf），docx/xlsx/扫描件存档待 OCR
- **审核报告 DOCX**：报告按钮同时产出 MD（权威）+ DOCX（仿宋/黑体/表格/页码域）
- **AI 点评**：审核结果页按钮，GLM 输出优先整改项与风险提示（辅助层，判定仍归规则引擎+人工）

## 数据

- 法规库：24 部（国家 16 + 省 6 + 市县 2），frontmatter 元数据 + 要点摘编，`data/regulations/*.md`
- 审核报告与归档卷输出：`data/outputs/`
