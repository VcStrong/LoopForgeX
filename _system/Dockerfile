# 控制面（知识库网页 + 任务队列 + MCP）容器镜像。
#   docker build -f _system/Dockerfile -t kb-control .        （在仓库根目录；一般直接 docker compose up -d）
# 数据全在 /data（KB_ROOT=/data/kb：期、资料；HOME=/data/home：引擎登录态、主密钥），挂卷保留。
# 元数据库：设 KB_DB_URL=postgresql://…（compose 默认）/ mysql://…；不设就是卷里的 SQLite（/data/kb/_system/kb.db）。
# 机密走加密文件（容器里没有钥匙串），主密钥默认 /data/home/.config/kb/master.key（首次自动生成，600）；
#   正式部署建议用 docker secret 挂进来并设 KB_MASTER_KEY_FILE=/run/secrets/kb_master_key。
# 构建参数 WITH_ENGINES=1：装 Node + Claude Code / Codex 命令行（管家、方案、本机兜底执行要用）；=0 镜像更小。
ARG BASE_IMAGE=python:3.12-slim
ARG NODE_IMAGE=node:22-slim
FROM ${NODE_IMAGE} AS node
FROM ${BASE_IMAGE}
ARG WITH_ENGINES=1
# 国内网络：APT_MIRROR=mirrors.aliyun.com、NPM_REGISTRY=https://registry.npmmirror.com、PIP_INDEX=https://mirrors.aliyun.com/pypi/simple
ARG APT_MIRROR=
ARG NPM_REGISTRY=
ARG PIP_INDEX=
RUN if [ -n "$APT_MIRROR" ]; then sed -i "s#deb.debian.org#$APT_MIRROR#g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; fi \
    && apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y --no-install-recommends git openssh-client openssl ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/*
# Node 从官方 node 镜像拷（比 apt 装 npm 少几百个包）；引擎命令行按需装
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && if [ "$WITH_ENGINES" = "1" ]; then npm install -g ${NPM_REGISTRY:+--registry $NPM_REGISTRY} @anthropic-ai/claude-code @openai/codex \
       && npm cache clean --force; fi
WORKDIR /kb
COPY _system/requirements.txt _system/requirements-db.txt /kb/_system/
RUN pip install --no-cache-dir ${PIP_INDEX:+-i $PIP_INDEX} -r /kb/_system/requirements.txt -r /kb/_system/requirements-db.txt
COPY kb /kb/kb
COPY _system /kb/_system
RUN chmod +x /kb/_system/docker-entrypoint.sh && printf '#!/bin/sh\nexec python /kb/_system/kb.py "$@"\n' > /usr/local/bin/kb && chmod +x /usr/local/bin/kb
ENV KB_ROOT=/data/kb HOME=/data/home KB_SECRET_BACKEND=file KB_HOST=0.0.0.0 KB_PORT=11555 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
VOLUME /data
EXPOSE 11555
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD curl -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:11555/login || exit 1
ENTRYPOINT ["tini", "--", "/kb/_system/docker-entrypoint.sh"]
CMD ["web"]
