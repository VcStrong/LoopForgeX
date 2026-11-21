# 知识库跑在 Docker 上

把知识库网页（控制面：网页 + 任务队列 + MCP）和执行机放进容器。适合：先在自己 Mac 的 Docker Desktop 上试团队模式，之后整套搬到 Linux 服务器。

## 结构

| 容器 | 镜像 | 做什么 | 数据 |
|---|---|---|---|
| `db` | `postgres:16` | 元数据库（默认 PostgreSQL）：用户、项目、期信息、记录、任务队列、执行机、资源池… | 卷 `kb-pg` |
| `control` | `_system/Dockerfile` | `kb web`（团队模式，要登录）、任务队列、MCP、定时任务 | 卷 `kb-data` → `/data`（`/data/kb` 是知识库根：期、资料文件；`/data/home` 放引擎登录态和主密钥） |
| `runner-1` | `_system/Dockerfile.runner` | `kb runner`：只出站连控制面领活，跑 AI 会话、编译 | 卷 `kb-runner-1` → `/data`（工作副本池、会话私有 HOME、`runner.json`） |

两者共用卷 `kb-shared`：控制面第一次启动时给 `KB_BOOTSTRAP_RUNNERS` 里的执行机各发一个注册令牌，执行机注册用掉后就删除，所以 `docker compose up` 一条命令就能连上，不用去网页复制令牌。

不放进容器的：
- **插 USB 的安卓真机、iOS 编译**：Mac 上的 Docker Desktop 不能把 USB 直通给容器。这些仍在 Mac 本机跑执行机：`kb runner --server http://localhost:11555 --token <网页「🖥 执行机」生成的注册令牌>`。
- **安卓编译**：执行机镜像里没有 Android SDK / JDK，要在容器里编译就在 `Dockerfile.runner` 基础上再加一层。

## 第一次启动

```sh
cp docker.env.example .env        # .env 不进仓库
# 编辑 .env：KB_DB_PASSWORD=数据库密码；管理员默认 admin / admin123（要换就设 KB_ADMIN_PASSWORD，至少 8 位）
docker compose up -d --build
```

打开 <http://localhost:11555>，用 `admin` / `admin123`（或 .env 里设的 KB_ADMIN_PASSWORD）登录，会要求马上改密码。「🖥 执行机」里能看到 `runner-1` 在线（自动标签 `linux`、`claude`、`codex`）。

国内网络拉不到 Docker Hub / Debian / npm / PyPI 时，在 `.env` 里打开 `KB_BASE_IMAGE`、`KB_NODE_IMAGE`、`KB_APT_MIRROR`、`KB_NPM_REGISTRY`、`KB_PIP_INDEX` 这几行（示例里给了能用的镜像源）。

常用命令：

```sh
docker compose logs -f                         # 看日志
docker compose exec control kb user add 张三    # 建账号（也可在网页「👥 成员」建）
docker compose exec control kb doctor          # 体检 / 迁移校验
docker compose down                            # 停；数据在卷里不会丢
```

## 数据库：PostgreSQL / MySQL / SQLite

元数据库按连接串选驱动（`KB_DB_URL`，或 `kb db use <连接串>` 写进 `_system/db.url`）：

| 场景 | 连接串 | 说明 |
|---|---|---|
| Docker / 服务器（默认） | `postgresql://kb:<密码>@db:5432/kb` | compose 自带 `db` 服务，不用改 |
| 已有 MySQL 8 | `mysql://用户:密码@主机:3306/库` | 库用 `utf8mb4`；compose 里也带了一个：`.env` 写 `KB_DB_URL=mysql://kb:<KB_DB_PASSWORD>@mysql:3306/kb`，启动加 `--profile mysql` |
| 已有 PostgreSQL | `postgresql://用户:密码@主机:5432/库` | PG 12+ |
| 本机单机安装 | 不配置 | SQLite 文件 `_system/kb.db`，零配置 |

```sh
kb db status                          # 当前驱动、连接（不显示密码）、表版本、行数
kb db copy --to postgresql://…        # 把当前库整库复制过去（目标已有数据要加 --force）
kb db use postgresql://…              # 以后用它（重启 kb web 生效）；kb db use sqlite 回到本机文件
```

PostgreSQL / MySQL 驱动在 `_system/requirements-db.txt`（控制面镜像已装好；本机用：`_system/.venv/bin/pip install -r _system/requirements-db.txt`）。执行机不连数据库，只走 HTTP。

## 把 Mac 上现在的知识库搬进来

不要直接把 Mac 上正在用的知识库目录挂进容器：两个 `kb web` 同时写一份库会坏数据。按搬家流程（REQ-034）走：

```sh
# 在 Mac 上（先停掉本机网页，避免导出时还在写）
kb backup --for-move --out ./backup/kb-move.tar.gz     # 会让你设一个口令，用来加密机密主密钥
# 放进容器
docker compose up -d control
docker compose exec control kb restore /backup/kb-move.tar.gz
docker compose exec control kb doctor
docker compose restart control
```

搬家包里的库是 SQLite 文件，恢复时自动导入当前驱动（容器里就是 PostgreSQL）。恢复后账号也换成 Mac 上的那些：Mac 上单机模式的「本机用户」没有密码，先给自己设一个再登录：`docker compose exec control kb user passwd <你的用户名>`。

Mac 上原来插手机的那台机器改成执行机，连容器里的控制面：`kb runner --server http://localhost:11555 --token <注册令牌>`（以前注册到别的控制面的，加 `--rebind` 保留原 ID）。

## AI 引擎登录

- 执行机上的会话用「⚙ 设置 → 🤖 AI 账号」里配的账号：控制面按会话把登录态下发到会话私有 HOME，会话结束就删，容器里不用手动登录。
- 控制面自己也会直接调引擎（管家、出方案）：第一次运行 `docker compose exec -it control claude` 登录一次，登录态存在卷里的 `/data/home`。
- 控制面容器默认在没有执行机在线时自己兜底跑会话；想全部交给执行机，`.env` 里设 `KB_NO_LOCAL_RUNNER=1`。

## 再加执行机

- 同一台机器：在 `docker-compose.yml` 里复制 `runner-1` 整段改成 `runner-2`（服务名、`KB_RUNNER_NAME`、卷名都改），`KB_BOOTSTRAP_RUNNERS=runner-1,runner-2`，再 `docker compose up -d`。
- 别的机器：网页「🖥 执行机 → 生成注册令牌」，在那台机器上 `./install.sh --runner --url <控制面地址> --token <令牌>`，或 `docker run -d -v kb-runner:/data -e KB_SERVER=<控制面地址> -e KB_RUNNER_TOKEN=<令牌> -e KB_RUNNER_NAME=<名称> kb-runner`。

## 安全

- 端口：本机 11555 已被占用（比如 Mac 上本来就开着知识库网页）时 `docker compose up` 会报 port is already allocated，在 `.env` 改 `KB_PUBLISH=127.0.0.1:11556` 再起；容器里的端口不用动。
- 端口默认只发布到 `127.0.0.1`；给局域网同事用时把 `KB_PUBLISH` 改成 `0.0.0.0:11555`，最好前面加 HTTPS 反向代理（执行机可用 `--ca` 信任自签证书）。
- 机密存在加密文件里（容器里没有钥匙串）。主密钥默认在卷里 `/data/home/.config/kb/master.key`；正式部署建议用 docker secret 挂进来，并设 `KB_MASTER_KEY_FILE=/run/secrets/kb_master_key`，让主密钥和数据分开保存。
- 执行机只出站连接控制面，不开任何端口。

## 不装 Docker 也能验证多执行机

`kb dev-cluster up --runners 2 [--docker]`：独立测试库 + 多个执行机进程（`--docker` 再加一台容器执行机）；`kb dev-cluster chaos all` 跑故障演练并出报告；`kb dev-cluster down` 收掉。
