# 桌面应用打包说明

## macOS dmg

在 macOS 项目根目录执行：

```bash
./scripts/build_macos_dmg.sh
```

产物：

```text
dist/PatentAgent.app
dist/PatentAgent-macOS.dmg
```

说明：

- 安装包包含项目代码、前端页面、`skills/` 和注册 tools。
- 安装包不包含 `.env`、`outputs/` 或本机历史记录。
- 用户运行后，在系统设置中填写自己的外部搜索 API 和 Pi Agent LLM 配置。
- 用户配置保存到本机用户目录下的 `user_config.json`，不会写回项目 `.env`。

## Windows exe

Windows exe 需要在 Windows 环境构建，不能直接在 macOS 上用 PyInstaller 交叉生成。

### 方案 A：GitHub Actions 自动构建

推送代码后，在 GitHub 仓库页面进入：

```text
Actions -> Build Windows EXE -> Run workflow
```

构建完成后，在该 workflow run 的 `Artifacts` 中下载：

```text
PatentAgent-Windows
```

GitHub 会下载一个 `PatentAgent-Windows.zip`。完整解压后，里面包含：

```text
PatentAgent\PatentAgent.exe
PatentAgent\_internal\...
```

运行：

```text
PatentAgent\PatentAgent.exe
```

不要只复制 `PatentAgent.exe`。Windows 版本采用便携目录打包，以避免单文件程序启动时解压原生依赖失败；`PatentAgent` 目录中的 `_internal` 等文件必须与 exe 保持在一起。

### 方案 B：Windows 本机手动构建

把项目放到 Windows 后，在 PowerShell 中执行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\build_windows_exe.ps1
```

产物：

```text
dist\PatentAgent\PatentAgent.exe
```

## API 配置

打包后的应用第一次启动时没有内置用户 API key。公司统一 LightRAG 不需要用户填写；进入“系统设置 -> 运行时 API 配置”填写：

- 外部搜索 API Key
- 外部搜索 API 地址
- Agent 核，默认 `pi_coding_agent`
- Pi Provider
- Pi Model
- Pi Agent LLM API Key，对应 `PI_AGENT_API_KEY`，运行时会按 provider 同步到对应环境变量

密码框留空不会覆盖已有 key；只有输入新 key 并保存时才会更新。
