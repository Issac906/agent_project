# 桌面 AI 接入说明

专利 Agent 同时保留桌面应用、MCP 和 JSON CLI 三个入口。三个入口调用同一套 Flask 后端，因此在 Codex 中选择的知识库、候选 idea、章节确认和最终历史记录，也能在桌面应用中查看。

## Codex MCP

源码运行时注册：

```bash
codex mcp add patent-agent -- \
  /absolute/path/to/python \
  /absolute/path/to/agent_project/patent_agent_mcp.py
```

打包后的 macOS DMG 附带 `PatentAgentMCP`，安装后可以注册该控制台程序：

```bash
codex mcp add patent-agent -- \
  /Applications/PatentAgentMCP/PatentAgentMCP \
  --mcp
```

Windows 便携包可双击根目录的 `Register-Codex.cmd` 自动注册。手动注册命令为：

```powershell
codex mcp add patent-agent -- `
  "C:\完整路径\PatentAgent\PatentAgentMCP\PatentAgentMCP.exe" `
  --mcp
```

Windows 发布包已经携带 Pi Coding Agent 与 Node.js。桌面软件和 MCP 桥接无论哪一个先启动，都会共享同一后端，并自动使用包内的 Pi；用户仍需在系统设置中填写自己的 LLM provider、model 和 API key。

其他支持 STDIO MCP 的桌面 AI，可把命令配置为：

```text
命令：C:\完整路径\PatentAgent\PatentAgentMCP\PatentAgentMCP.exe
参数：--mcp
```

建议把 MCP 工具调用超时设置为 900 秒，因为知识库查询、外部检索和章节生成可能需要数分钟。

## JSON CLI

不支持 MCP、但可以执行本地命令的客户端，可以调用：

```bash
python patent_agent_cli.py tools
python patent_agent_cli.py call patent_list_knowledge_bases '{}'
python patent_agent_cli.py call patent_start_run '{"knowledge_base_id":"all","innovation_level":"medium"}'
```

打包应用对应：

```text
macOS: /Applications/PatentAgentMCP/PatentAgentMCP --cli tools
Windows: PatentAgentMCP\PatentAgentMCP.exe --cli tools
Windows: PatentAgentMCP\PatentAgentMCP.exe --cli call patent_get_run {"run_id":"..."}
```

CLI 的标准输出始终是 JSON，适合其他 AI 助手或自动化程序解析。

## 对话流程

1. 调用 `patent_list_knowledge_bases`，让用户选择知识库和创新档位。
2. 调用 `patent_start_run`。系统自动读取图谱、评估素材、外部检索、生成候选 idea 和相似专利分析。
3. 把候选 idea 展示给用户，再调用 `patent_select_idea`。
4. 每章生成后先展示内容。根据用户意见调用接受、重写、按意见修改或手动编辑工具。
5. 最后一章接受后，返回 Markdown、Word、分析报告下载地址，并写入原有历史记录。

桌面应用仍用于知识库管理、系统设置、知识图谱浏览和历史记录查看。API Key 不通过 MCP 返回，也不会写进聊天记录。
