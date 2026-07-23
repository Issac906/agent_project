# Patent Agent 公司服务器部署

部署包面向 `linux/amd64`（x86_64）服务器，包含 Patent Agent Web 应用、Pi Coding Agent、项目 Skills、Tools、飞书机器人、定时任务以及 Python/Node 运行依赖。它还包含可选的独立知识库管理服务镜像；公司总 LightRAG、模型 API、外部搜索 API 仍通过公司网络连接，不会复制进镜像。

## 一、解压部署包

把 `patent-agent-server-amd64.tar.gz` 上传到服务器后执行：

```bash
tar -xzf patent-agent-server-amd64.tar.gz
cd patent-agent-server-amd64
```

目录中包含：

```text
patent-agent-amd64.tar
company.env.example
kb-manager.env.example
DOCKER_DEPLOY.md
run_patent_agent.sh
run_kb_manager.sh
```

## 二、加载 Docker 镜像

```bash
docker load -i patent-agent-amd64.tar
docker image inspect patent-agent:latest --format '{{.Os}}/{{.Architecture}}'
docker image inspect patent-kb-manager:latest --format '{{.Os}}/{{.Architecture}}'
```

第二条命令应输出：

```text
linux/amd64
```

## 三、配置服务器环境

```bash
cp company.env.example company.env
vi company.env
```

至少检查以下配置：

```dotenv
LIGHTRAG_BASE_URL=http://192.168.130.130:9621/webui/#/
KB_MANAGER_URL=http://192.168.130.130:9700
PI_CODING_PROVIDER=deepseek
PI_CODING_MODEL=deepseek-chat
DEEPSEEK_API_KEY=服务器实际使用的key
SEARCH_PROVIDER=anysearch
ANYSEARCH_BASE_URL=实际搜索API地址
SEARCH_API_KEY=实际搜索key
```

飞书机器人也可部署后在“系统设置”中配置。若直接使用环境变量，请填写 `FEISHU_ENABLED`、`FEISHU_APP_ID`、`FEISHU_APP_SECRET` 和用户可访问的 `FEISHU_PUBLIC_BASE_URL`。

不要把真实 key 写回 `company.env.example`，也不要把包含 key 的 `company.env` 上传到 Git。

## 四、启动应用

```bash
chmod +x run_patent_agent.sh
./run_patent_agent.sh
```

浏览器访问：

```text
http://服务器IP:5000
```

若服务器的 5000 端口已占用：

```bash
HOST_PORT=5001 ./run_patent_agent.sh
```

然后访问 `http://服务器IP:5001`。

飞书长连接、飞书定时任务和手动消息交互均由同一个 Patent Agent 容器持续运行，不需要单独启动第二个飞书进程。服务器需要能主动访问 `https://open.feishu.cn`。

## 五、可选：启用独立知识库自动管理

只有需要让用户在应用内新建“物理隔离的知识库”时才执行本节。总知识库查询与飞书功能不依赖该服务。

```bash
cp kb-manager.env.example kb-manager.env
vi kb-manager.env
chmod +x run_kb_manager.sh
./run_kb_manager.sh
```

在 `kb-manager.env` 中至少确认：

- `KB_MANAGER_API_KEY`：设置一个随机管理密钥；
- `KB_MANAGER_PUBLIC_HOST`：服务器的公司内网 IP；
- `KB_MANAGER_LIGHTRAG_ENV_FILE`：总 LightRAG 使用的模型/存储环境文件路径；
- `LIGHTRAG_API_KEY`：若 LightRAG 开启鉴权，则与其保持一致。

随后把主应用 `company.env` 内的 `KB_MANAGER_URL` 改为 `http://服务器IP:9700`，并将 `KB_MANAGER_API_KEY` 设为同一个值，重启 `patent-agent` 容器。

## 六、持久化内容

启动脚本会创建：

- `data/`：历史记录、飞书会话、定时任务、知识库分组和网页系统设置；
- `outputs/`：兼容输出文件。

升级镜像或重建容器时保留这两个目录，已有数据不会丢失。

## 七、维护命令

```bash
docker logs -f patent-agent
docker restart patent-agent
docker stop patent-agent
docker rm -f patent-agent
docker logs -f patent-kb-manager
```

应用启动失败时先运行：

```bash
docker logs --tail 200 patent-agent
```

## 八、网络要求

- 服务器必须能访问 LightRAG、知识库管理服务、Pi 所用模型 API、外部搜索 API 和飞书 OpenAPI。
- 容器里的 `127.0.0.1` 指容器自身。访问宿主机或其他内网服务时请填写真实内网 IP 或 Docker 网络服务名。
- 如果使用飞书长连接，服务器不需要公网回调地址，但必须允许容器主动访问 `https://open.feishu.cn`。
