# LightRAG 专利写作 Agent Workflow

这是一个基于 LightRAG 知识库 API、外部检索、Pi coding agent、Skills 和 Tools 的交互式专利发现与写作 agent。当前默认模式不是把流程完全写死，而是启动一个 agent loop：先加载项目内 skills，再根据当前状态选择下一个工具，并在关键节点和用户交互。

```text
读取知识库材料
↓
评估素材是否充分
↓
无论是否充分都进行外部专利相关搜索
↓
自动提出多个可能的专利方向
↓
生成相似专利差异分析表
↓
用户选择要继续的专利方向
↓
按章节逐步写作，每一步由用户确认、重写或手动编辑
↓
对标检查
↓
输出最终文档
```

当前版本提供 Web 前端，也保留命令行交互流程。正式使用前仍需要在国家知识产权局专利检索及分析系统做人工检索核对。

## Agent + Skills + Tools 架构

### Skills

启动时会加载：

- `skills/patent-writing/SKILL.md`：项目内专利撰写主 skill，已从本地 `~/.codex/skills/patent-writing` 复制而来。
- `skills/agent-planning/SKILL.md`：agent 决策规则。
- `skills/material-assessment/SKILL.md`：素材充分性判断标准。
- `skills/prior-art-analysis/SKILL.md`：相似专利差异分析标准。
- `skills/interactive-drafting/SKILL.md`：分步骤写作和用户交互规则。
- `skills/patent-quality-review/SKILL.md`：标题、背景问题链、创新点、有益效果证据和保护范围的强制验收标准。
- `skills/formula-formatting/SKILL.md`：公式 LaTeX 源格式、变量定义、网页与 Word 展示标准。

### Tools

agent 可调用的工具包括：

- `read_knowledge_base`：读取 LightRAG 文档和状态；
- `assess_materials`：评估素材是否足够；
- `external_search`：执行外部专利相关搜索；
- `propose_candidates`：提出候选专利方向；
- `analyze_similar_patents`：生成相似专利差异分析 Excel/Markdown；
- `select_candidate`：让用户选择专利方向；
- `draft_interactively`：逐章节生成技术交底书并与用户交互；
- `review_patent_quality`：逐章检查并自动修订不合格内容，最终导出前再做全文复核；
- `save_outputs`：保存最终文档。

写作质量不再只依赖提示词。`patent_quality_tool.py` 会在章节生成、重写或修改后执行确定性检查；发现问题后，agent 根据检查结果自动修订并再次检查。网页会显示当前章节和最终文档的质量分及未解决项。

项目中的 Skill 由 `agent_skill_loader.py` 自动扫描 `skills/*/SKILL.md`，Tool 通过 `@register_tool` 注册。首页“系统设置”页面会动态显示当前实际加载的全部 Skills 和 Tools。

公式统一使用标准 LaTeX：行内 `$...$`、独立公式 `$$...$$`。网页通过 MathJax 渲染，Word 通过 `latex2mathml` 转换为原生 OMML 公式对象。

### Agent Loop

每轮 agent 会根据当前状态、已加载 skills 和工具列表选择下一步。当 `AGENT_CORE=pi_coding_agent` 时，planner、候选专利生成和章节写作会通过本机 `pi` 命令执行，并使用 Pi coding agent 中配置的 DeepSeek API；后端仍保留证据门槛，确保不会跳过知识库读取、外部检索、相似专利分析和用户选择这些关键步骤。

## 项目结构

```text
.
├── main.py                 # 命令行入口
├── app.py                  # Web 前端入口
├── patent_discovery_agent.py # 交互式专利发现与分步骤写作
├── agent_skill_loader.py   # 加载项目内 skills
├── pi_coding_agent_client.py # Pi coding agent 核调用封装
├── workflow.py             # 串联完整 agent workflow
├── config.py               # 读取 .env 配置
├── skill_router.py         # 根据任务判断输出类型
├── lightrag_client.py      # LightRAG Server API 客户端
├── writer.py               # 专利文档 Markdown 生成
├── patent_quality_tool.py  # 专利章节与最终文档质量门禁
├── formula_utils.py        # LaTeX 规范化、公式检查和 Word OMML 转换
├── tool_registry.py        # 动态 Tool 注册表
├── evaluator.py            # 和 skill/示例章节对标检查
├── external_search.py      # 外部网页搜索 fallback
├── llm_writer.py           # 历史兼容模块；当前 Web/CLI agent 默认不走它
├── similar_patent_analysis.py # 相似专利差异分析 Excel/Markdown 生成
├── models.py               # 数据模型
├── requirements.txt
├── .env.example
├── templates/
├── static/
├── skills/
│   ├── patent-writing/
│   ├── agent-planning/
│   ├── material-assessment/
│   ├── prior-art-analysis/
│   ├── interactive-drafting/
│   ├── patent-quality-review/
│   └── formula-formatting/
└── outputs/
```

## 配置

复制配置模板：

```bash
cp .env.example .env
```

`.env` 示例：

```bash
LIGHTRAG_BASE_URL=http://192.168.130.130:9621/webui/#/
LIGHTRAG_API_KEY=
LIGHTRAG_QUERY_MODE=mix
LIGHTRAG_TOP_K=
LIGHTRAG_INCLUDE_CHUNK_CONTENT=true

AGENT_CORE=pi_coding_agent
PI_CODING_COMMAND=pi
PI_CODING_PROVIDER=deepseek
PI_CODING_MODEL=deepseek-chat
PI_CODING_TIMEOUT=600

SEARCH_PROVIDER=anysearch
ANYSEARCH_BASE_URL=你的 AnySearch 搜索接口地址
ANYSEARCH_TIMEOUT=10
SEARCH_API_KEY=
```

### 1. LightRAG 知识库 API

- `LIGHTRAG_BASE_URL` 是 LightRAG Server 地址。即使填 Web UI 地址，代码也会自动规范化到 API root。
- `LIGHTRAG_API_KEY` 是 LightRAG Server API 的鉴权 key，不是 OpenAI key。
- 如果 LightRAG Server 没开鉴权，`LIGHTRAG_API_KEY` 留空即可。
- `LIGHTRAG_QUERY_MODE` 是知识库查询模式。你的 OpenAPI 显示可选值是 `local`、`global`、`hybrid`、`naive`、`mix`、`bypass`，默认建议先用 `mix`。
- `LIGHTRAG_TOP_K` 是可选检索数量参数。如果你的 Swagger 不支持，先留空。
- `LIGHTRAG_INCLUDE_CHUNK_CONTENT` 控制 references 是否尽量包含原始 chunk 内容，建议先保持 `true`。

### 2. Agent 核选择

默认建议使用 Pi coding agent：

```bash
AGENT_CORE=pi_coding_agent
PI_CODING_COMMAND=pi
PI_CODING_PROVIDER=deepseek
PI_CODING_MODEL=deepseek-chat
PI_CODING_TIMEOUT=600
```

- `AGENT_CORE=pi_coding_agent`：使用本机 Pi coding agent 作为 agent 核。
- `PI_CODING_COMMAND`：Pi coding agent 命令名，通常是 `pi`。
- `PI_CODING_PROVIDER`：当前使用 `deepseek`。
- `PI_CODING_MODEL`：默认 `deepseek-chat`。
- `PI_CODING_TIMEOUT`：单次 Pi 调用超时时间。

DeepSeek 的 key/登录配置由 Pi coding agent 自己管理，本项目不单独配置 DeepSeek API。

### 3. 外部搜索 API

当前推荐使用 `anysearch`，通过 API 做外部资料和相似专利检索；公共网页搜索只作为备用，不适合作为稳定生产能力。

- `SEARCH_PROVIDER`：搜索服务商；当前填 `anysearch`。
- `ANYSEARCH_BASE_URL`：AnySearch 搜索接口地址。如果官方给的是完整 `/search` 接口，直接填完整地址；如果填基础地址，程序会自动拼接 `/search`。
- `ANYSEARCH_TIMEOUT`：AnySearch 单次请求超时时间，默认 `10` 秒。
- `SEARCH_API_KEY`：AnySearch API key。

### 你现在最少需要填什么

如果只测试知识库：

```bash
LIGHTRAG_BASE_URL=你的 LightRAG Web UI 或 API 地址
LIGHTRAG_API_KEY=如果 LightRAG 需要鉴权才填
```

如果要用 Pi coding agent 作为 agent 核：

```bash
AGENT_CORE=pi_coding_agent
PI_CODING_COMMAND=pi
PI_CODING_PROVIDER=deepseek
PI_CODING_MODEL=deepseek-chat
```

同时确保 Pi coding agent 能访问 DeepSeek API：

```bash
pi --provider deepseek --model deepseek-chat -p "hello"
```

## 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 测试知识库 API

```bash
python main.py --check
```

该命令会检查：

- `GET /documents`
- `GET /documents/status_counts`

## 运行 Workflow

### Web 前端

启动网页：

```bash
python app.py
```

然后打开：

```text
http://127.0.0.1:5000
```

如果 5000 端口被占用，程序会自动换到 5001、5002 等可用端口，请以终端输出的地址为准。也可以在 `.env` 中指定：

```bash
WEB_PORT=5050
```

首页会简要展示知识库文档和处理状态，提供“刷新知识库”和“生成专利方案”两个操作。进入生成流程后，页面会按步骤推进知识库读取、素材评估、外部检索、候选专利、相似专利差异分析和分章节写作；需要用户确认的节点会在网页中显示选择、接受、重写、修改意见和手动编辑操作。

### Docker 打包部署

如果需要导出给公司服务器使用的 Docker tar 包，先确保本机已安装并启动 Docker，然后执行：

```bash
./scripts/build_docker_tar.sh
```

生成文件：

```text
patent-agent.tar
```

公司服务器部署步骤见：

```text
deploy/DOCKER_DEPLOY.md
```

镜像内会安装 Pi coding agent；公司服务器不需要预装 `pi`，但需要在运行时通过 `company.env` 配置 LightRAG 地址和 DeepSeek 凭据。

### 交互式专利发现流程

默认运行：

```bash
python main.py
```

流程会自动：

- 读取 LightRAG 已处理文档；
- 计算素材充分性分数；
- 不管素材是否充分，都执行外部搜索；
- 生成多个候选专利方向；
- 根据外部搜索结果，为每个候选方向生成相似专利差异分析；
- 让用户选择一个方向；
- 按标题页、发明名称、技术领域、背景技术、发明内容、附图说明、具体实施方式、附图逐步写作；
- 每一步都可选择接受、重写、手动编辑或结束。

输出文件：

```text
outputs/interactive_patent_draft.md
outputs/result.md
outputs/similar_patent_analysis.xlsx
outputs/similar_patent_analysis.md
```

`similar_patent_analysis.xlsx` 会参考样例表格结构，为每个候选专利方向创建一个 sheet，列包括：公开号、申请号、申请日、发明名称、申请人、摘要、差异点。当前表格基于外部网页检索摘要自动整理，只能用于初筛，正式提交前仍需人工核对专利全文、权利要求和法律状态。

### 直接生成模式

如果你仍然想跳过候选选择，直接按输入任务生成：

生成技术交底书：

```bash
python main.py --direct "为基于 LightRAG 知识库 API 的专利写作 agent workflow 撰写技术交底书"
```

如果 LightRAG 没有检索到上下文，程序会自动尝试外部搜索。也可以手动强制启用：

```bash
python main.py --direct --enable-external-search "为基于 LightRAG 知识库 API 的专利写作 agent workflow 撰写技术交底书"
```

生成选题分析：

```bash
python main.py --direct "分析基于知识库 API 的专利写作 agent 的创新点和可专利性"
```

生成附图全集：

```bash
python main.py --direct "为这个专利写作 agent workflow 生成附图 Mermaid"
```

同时调试 `/query/data`：

```bash
python main.py --direct --with-query-data "为基于 LightRAG 知识库 API 的专利写作 agent workflow 撰写技术交底书"
```

输出文件会写入 `outputs/`，并同时更新兼容文件：

```text
outputs/result.md
```

根据任务类型，还会生成：

- `outputs/technical_disclosure.md`
- `outputs/topic_analysis.md`
- `outputs/patent_figures.md`

## 关于 `/query` 请求体

当前默认请求体：

```json
{
  "query": "用户问题",
  "mode": "mix",
  "only_need_context": false,
  "only_need_prompt": false,
  "response_type": "Multiple Paragraphs",
  "include_references": true,
  "include_chunk_content": true
}
```

如果服务端返回 400 或 422，代码会自动回退为：

```json
{
  "query": "用户问题"
}
```

如果你的 Swagger/OpenAPI 文档要求其他字段，请调整 `LightRAGClient._build_query_payload()`。

## 如果输出仍然 no-context

`[no-context]` 表示 API 通了，但知识库没有检索到可用上下文。请检查：

- 文档是否已经上传到 LightRAG 知识库；
- 文档处理状态是否完成；
- 问题是否和知识库内容匹配；
- `/query` 的 `mode`、`top_k` 等参数是否符合你的 LightRAG Server 版本；
- `/query/data` 是否能返回原始检索片段。

## 飞书机器人与自定义定时生成

应用支持通过飞书企业自建应用机器人的长连接复用完整专利生成流程。网页端和飞书端使用同一个 Agent 状态机，因此飞书中的知识库选择、候选 idea、用户选择、章节全文、重写意见、最终文件和历史记录保持一致。

飞书中的专利章节使用消息卡片展示。标题、列表和段落保留可读排版；LaTeX 公式与 Mermaid 附图会先转换为 PNG、上传飞书，再嵌入卡片，因此聊天窗口不会显示原始公式代码或 Mermaid 源码。

## 物理隔离知识库

“总知识库”继续使用 `LIGHTRAG_BASE_URL`。项目提供独立的知识库管理服务，用户在首页选择“新建知识库”并上传第一份素材后，服务会自动完成：

- 分配独立 workspace 和端口；
- 创建独立 `rag_storage` 与 `inputs` 目录；
- 启动独立 LightRAG Docker 容器；
- 等待健康检查通过并登记 API/图谱地址；
- 将当前素材上传到新实例。

管理服务只部署在公司服务器一次。准备配置：

```bash
cd deploy
cp kb-manager.env.example kb-manager.env
# 编辑 kb-manager.env，至少填写管理令牌、服务器地址和复用的 LightRAG 配置文件
docker compose --env-file kb-manager.env -f docker-compose.kb-manager.yml up -d --build
```

`KB_MANAGER_LIGHTRAG_ENV_FILE` 指向一份经过验证的 LightRAG 环境文件，新实例会复用其中的 LLM、Embedding 和存储配置，但强制覆盖 `WORKSPACE`、`WORKING_DIR`、`INPUT_DIR`、`HOST` 与容器内端口。随后在 Patent Agent 的 `.env` 或“系统设置 → 知识库实例管理”中配置：

```dotenv
KB_MANAGER_URL=http://192.168.130.130:9700
KB_MANAGER_API_KEY=<与管理服务一致的随机令牌>
KB_MANAGER_TIMEOUT=240
```

素材上传、删除、查询、生成和“查看该知识库的知识图谱”都会只访问所选实例。删除由应用创建的知识库时，管理服务停止容器并移除登记，但默认保留数据目录，避免误删后无法恢复。旧版逻辑分组仍可由管理员手动绑定独立实例并迁移素材。

管理服务需要访问 `/var/run/docker.sock`，因此必须只在受信任的公司服务器运行；`9700` 端口应通过防火墙限制为内网访问，并使用足够长的 `KB_MANAGER_API_KEY`。不要在桌面安装包中写死该令牌。

如果公司服务器无法访问 Docker Hub 或 PyPI，可先在联网电脑执行
`python -m pip download --no-deps docker==7.1.0 -d vendor`，再使用
`Dockerfile.kb-manager.offline` 基于服务器已有的 `patent-agent:latest`
镜像离线构建。`vendor/*.whl` 不提交到 Git。

安装依赖后，在“系统设置 → 飞书机器人”中填写：

- App ID（`cli_...`）
- App Secret
- 应用可访问地址（可选，用于在飞书中打开文件和历史记录）

在飞书开放平台创建企业自建应用后，需要添加机器人能力，在“事件与回调”中选择长连接并订阅 `im.message.receive_v1`，开通读取私聊/群聊消息和以应用身份发消息的权限，然后发布应用并把测试用户加入可用范围。不要把 App Secret 写进代码或提交到 Git。

群内可用指令：

```text
开始生成
状态
查看会话ID
选择 2
接受
重写
修改：请补充控制参数的边界条件
结束并保存
```

候选 idea 和章节正文过长时会拆成多条连续聊天消息，但不会截断或用省略号代替。用户接受当前章节后，Agent 会自动生成并发送下一章。

定时生成在系统设置中逐条配置，支持每天、工作日、每周、每月和任意五段 Cron 表达式。每条计划可以设置时区、群/用户目标、启动消息、知识库和创新档位。到点后系统按计划配置自动创建运行并推进到候选 idea，候选选择和后续章节仍由用户逐步确认。非计划时间内，用户也可以随时在飞书中发送“开始生成”启动完整交互流程。

常用 Cron 示例：

```text
0 9 * * *    每天 09:00
0 9 * * 1    每周一 09:00
0 9 1 * *    每月 1 日 09:00
0 9 * * 1-5  工作日 09:00
```

如果要让飞书消息中的下载链接可用，`FEISHU_PUBLIC_BASE_URL` 必须是飞书用户可访问的公司内网或公网地址，不能填只在应用本机有效的 `127.0.0.1`。
