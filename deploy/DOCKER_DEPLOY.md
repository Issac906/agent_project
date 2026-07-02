# Docker 打包与公司服务器部署

## 1. 本机打包镜像

在项目根目录执行：

```bash
docker build -t patent-agent:latest .
docker save -o patent-agent.tar patent-agent:latest
```

把下面两个文件带到公司服务器：

```text
patent-agent.tar
deploy/company.env.example
```

## 2. 公司服务器加载镜像

```bash
docker load -i patent-agent.tar
cp company.env.example company.env
```

编辑 `company.env`：

```bash
LIGHTRAG_BASE_URL=http://公司内网 LightRAG 地址:端口/webui/#/
LIGHTRAG_API_KEY=如果 LightRAG 需要鉴权才填
DEEPSEEK_API_KEY=公司可用的 DeepSeek API key
```

如果公司已经用其他方式给 pi coding agent 配好了凭据，也可以不填 `DEEPSEEK_API_KEY`。

## 3. 启动容器

```bash
docker run --name patent-agent \
  --env-file company.env \
  -p 5000:5000 \
  -v "$(pwd)/outputs:/app/outputs" \
  patent-agent:latest
```

浏览器打开：

```text
http://服务器IP:5000
```

`outputs` 目录会保存生成的 Markdown、Word 和相似专利差异分析 Excel。

## 4. 常用维护命令

停止：

```bash
docker stop patent-agent
```

重新启动：

```bash
docker start patent-agent
```

查看日志：

```bash
docker logs -f patent-agent
```

删除旧容器：

```bash
docker rm -f patent-agent
```

## 5. 注意事项

- 容器内已经安装 `@earendil-works/pi-coding-agent`，公司服务器不需要预装 `pi`。
- `DEEPSEEK_API_KEY` 不要写进 Dockerfile，也不要打进镜像；只在公司服务器的 `company.env` 中配置。
- 如果 LightRAG 运行在公司服务器宿主机本机，不要在容器里填 `http://127.0.0.1:9621`，因为容器里的 `127.0.0.1` 是容器自己。Linux 上通常应填宿主机内网 IP，或使用 Docker 网络。
- 如果公司网络不能访问 npm，必须在你本机先成功 `docker build`，然后用 `docker save` 导出的 `patent-agent.tar` 部署；公司服务器只需要 `docker load`，不需要联网安装依赖。
