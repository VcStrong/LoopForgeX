#!/bin/bash
# From 大大王.DT
# 知识库一键安装：建 Python 环境、初始化空知识库、加 kb 命令、接入 Claude Code。可重复运行。macOS（本机）和 Linux（远端服务器）都行。
# 用法：./install.sh [--autostart] [--host 0.0.0.0] [--port 11555（写进 _system/web.json，之后 kb port set 可改）] [--no-mcp] [--no-alias] [--no-open]
#       ./install.sh --server                                  控制面（团队服务器：听 0.0.0.0、开机自启，提示 kb user add 建管理员）
#       ./install.sh --runner --url <控制面地址> --token <注册令牌> [--name 名称] [--ca 证书]   执行机（只出站，常驻 kb runner）
# 一行安装（还没下载程序时）：curl -fsSL https://raw.githubusercontent.com/VcStrong/knowledge-base-public/main/install.sh | bash
set -euo pipefail

DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd || pwd)
if [ ! -f "${DIR}/_system/kb.py" ]; then          # 通过 curl | bash 运行：先把程序下载到 ~/knowledge-base（或 KB_DIR）
  REPO_URL="${KB_REPO:-https://github.com/VcStrong/knowledge-base-public.git}"
  DEST="${KB_DIR:-${HOME}/knowledge-base}"
  command -v git >/dev/null || { echo "需要 git（macOS：xcode-select --install；Linux：apt install git）" >&2; exit 1; }
  if [ -d "${DEST}/.git" ]; then git -C "${DEST}" pull -q --ff-only || true
  else git clone -q "${REPO_URL}" "${DEST}" || { echo "下载失败：${REPO_URL}" >&2; exit 1; }; fi
  exec bash "${DEST}/install.sh" "$@"
fi
AUTOSTART=0; MCP=1; ALIAS=1; HOST=127.0.0.1; PORT=11555; PORT_SET=0; OPEN=1; MODE=local; HOST_SET=0
RUNNER_URL=""; RUNNER_TOKEN=""; RUNNER_NAME=""; RUNNER_CA=""
while [ $# -gt 0 ]; do
  case "$1" in
    --autostart) AUTOSTART=1 ;;
    --host) HOST="$2"; HOST_SET=1; shift ;;
    --server) MODE=server ;;
    --runner) MODE=runner ;;
    --url) RUNNER_URL="$2"; shift ;;
    --token) RUNNER_TOKEN="$2"; shift ;;
    --name) RUNNER_NAME="$2"; shift ;;
    --ca) RUNNER_CA="$2"; shift ;;
    --port) PORT="$2"; PORT_SET=1; shift ;;
    --no-mcp) MCP=0 ;;
    --no-alias) ALIAS=0 ;;
    --no-open) OPEN=0 ;;
    -h|--help) sed -n 2,5p "$0" | sed 's/^# //'; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 1 ;;
  esac
  shift
done
OS=$(uname)
if [ "${MODE}" = server ]; then AUTOSTART=1; OPEN=0; [ "${HOST_SET}" = 1 ] || HOST=0.0.0.0; fi
if [ "${MODE}" = runner ]; then
  MCP=0; OPEN=0; AUTOSTART=0
  [ -n "${RUNNER_URL}" ] || { echo "执行机安装要给控制面地址：--url https://<控制面>" >&2; exit 1; }
fi

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

echo "知识库安装：${DIR}"

# 1. 系统与 Python
if [ "${OS}" = Darwin ]; then ok "macOS：密码存钥匙串"
else
  ok "${OS}：密码存加密文件（知识库目录 .机密/，主密钥 ~/.config/kb/master.key 或环境变量 KB_MASTER_KEY）"
  command -v openssl >/dev/null || warn "没有 openssl：存不了基础信息里的密码（apt install openssl）"
  command -v convert >/dev/null || command -v magick >/dev/null || warn "没有 ImageMagick：PSD 设计稿不能自动转预览图（可选：apt install imagemagick）"
  command -v soffice >/dev/null || warn "没有 LibreOffice：.doc / .rtf 不能转文字，.docx 不受影响（可选）"
fi
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  p=$(command -v "$c" 2>/dev/null) || continue
  if "$p" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then PY=$p; break; fi
done
[ -n "${PY}" ] || die "需要 Python 3.9 以上（macOS：brew install python；Linux：apt install python3 python3-venv）"
ok "Python：$("${PY}" --version 2>&1)（${PY}）"

# 2. 虚拟环境与依赖
VENV="${DIR}/_system/.venv"
if [ ! -x "${VENV}/bin/python" ]; then
  "${PY}" -m venv "${VENV}" || die "建虚拟环境失败"
fi
"${VENV}/bin/python" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
"${VENV}/bin/python" -m pip install -q -r "${DIR}/_system/requirements.txt" || die "安装依赖失败（需要能访问 PyPI）"
ok "依赖已安装：$(tr '\n' ' ' < "${DIR}/_system/requirements.txt")"
"${VENV}/bin/python" - <<'PY' || die "这个 Python 的 SQLite 不支持全文检索（FTS5），请换用 Homebrew 的 Python"
import sqlite3
sqlite3.connect(':memory:').execute('create virtual table t using fts5(x)')
PY
ok "SQLite 全文检索可用"

if [ "${MODE}" = runner ]; then
  # 执行机：只装程序和依赖，注册后常驻 kb runner（只出站连接控制面，不监听端口；Linux 不用 osascript / LaunchAgent / 钥匙串）
  chmod +x "${DIR}/kb"
  RUN_ARGS=(runner --server "${RUNNER_URL}")
  [ -n "${RUNNER_CA}" ] && RUN_ARGS+=(--ca "${RUNNER_CA}")
  REG_ARGS=("${RUN_ARGS[@]}" --register-only)
  [ -n "${RUNNER_TOKEN}" ] && REG_ARGS+=(--token "${RUNNER_TOKEN}")
  [ -n "${RUNNER_NAME}" ] && REG_ARGS+=(--name "${RUNNER_NAME}")
  "${DIR}/kb" "${REG_ARGS[@]}" || die "注册失败（注册令牌无效或已用过，到网页「⚙ 设置 → 🖥 执行机」重新生成）"
  ok "已注册为执行机（令牌存在 ~/.kb-runner/runner.json，权限 600）"
  ENVPATH="$(dirname "$(command -v claude || echo /usr/bin/true)"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
  LOG="${HOME}/.kb-runner/runner.log"
  if [ "${OS}" = Darwin ]; then
    PL="${HOME}/Library/LaunchAgents/com.local.kb.runner.plist"
    mkdir -p "$(dirname "${PL}")"
    ARGS_XML=""; for x in "${RUN_ARGS[@]}"; do ARGS_XML="${ARGS_XML}<string>${x}</string>"; done
    cat > "${PL}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.local.kb.runner</string>
  <key>ProgramArguments</key><array><string>${DIR}/kb</string>${ARGS_XML}</array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>${ENVPATH}</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${LOG}</string>
  <key>StandardErrorPath</key><string>${LOG}</string>
</dict></plist>
PLIST
    launchctl bootout "gui/$(id -u)" "${PL}" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$(id -u)" "${PL}" && ok "执行机已常驻（登录后自动启动，日志 ${LOG}）"
  elif command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
    UNIT="${HOME}/.config/systemd/user/kb-runner.service"
    mkdir -p "$(dirname "${UNIT}")"
    cat > "${UNIT}" <<UNITF
[Unit]
Description=知识库执行机
After=network-online.target

[Service]
ExecStart=${DIR}/kb ${RUN_ARGS[*]}
Environment=PATH=${ENVPATH}
Restart=always
RestartSec=5
StandardOutput=append:${LOG}
StandardError=append:${LOG}

[Install]
WantedBy=default.target
UNITF
    systemctl --user daemon-reload && systemctl --user enable --now kb-runner.service && ok "执行机已常驻（systemd --user，日志 ${LOG}）"
    loginctl show-user "$(id -un)" -p Linger 2>/dev/null | grep -q yes || warn "没退出登录也要一直跑：sudo loginctl enable-linger $(id -un)"
  else
    nohup "${DIR}/kb" "${RUN_ARGS[@]}" >> "${LOG}" 2>&1 &
    ok "执行机已在后台运行（nohup，重启机器后要重新运行；日志 ${LOG}）"
  fi
  echo; echo "执行机安装完成：到网页「⚙ 设置 → 🖥 执行机」看它是否在线。"
  exit 0
fi

# 3. 初始化空知识库（已有数据不会动）
chmod +x "${DIR}/kb"
"${DIR}/kb" init >/dev/null
ok "知识库目录已就绪（期/、收件箱/、索引）"

# 4. kb 命令
if [ "${ALIAS}" = 1 ]; then
  case "${SHELL:-}" in
    */bash) [ "${OS}" = Darwin ] && RC="${HOME}/.bash_profile" || RC="${HOME}/.bashrc" ;;
    *) RC="${HOME}/.zshrc" ;;
  esac
  touch "${RC}"
  LINE="alias kb=\"${DIR}/kb\""
  if grep -q '^alias kb=' "${RC}"; then
    if grep -qxF "${LINE}" "${RC}"; then ok "kb 命令已在 ${RC}"
    else
      cp "${RC}" "${RC}.bak-kb"
      grep -v '^alias kb=' "${RC}.bak-kb" > "${RC}"; echo "${LINE}" >> "${RC}"
      ok "已更新 ${RC} 里的 kb 命令（旧文件备份为 ${RC}.bak-kb）"
    fi
  else
    printf '\n# 本地知识库\n%s\n' "${LINE}" >> "${RC}"; ok "已在 ${RC} 加入 kb 命令（新开终端生效）"
  fi
fi

# 5. 接入 Claude Code（用户级 MCP，所有目录可用）
CLAUDE=$(command -v claude || true)
for c in /opt/homebrew/bin/claude /usr/local/bin/claude "${HOME}/.local/bin/claude"; do [ -n "${CLAUDE}" ] || { [ -x "$c" ] && CLAUDE=$c; }; done
if [ "${MCP}" = 1 ]; then
  if [ -n "${CLAUDE}" ]; then
    "${CLAUDE}" mcp remove kb -s user >/dev/null 2>&1 || true
    "${CLAUDE}" mcp add kb -s user -- "${VENV}/bin/python" "${DIR}/_system/kb.py" serve >/dev/null && ok "已接入这台机器上的 Claude Code（MCP 服务 kb，用户级）"
  else
    warn "没找到 claude 命令：装好 Claude Code 后重新运行 ./install.sh 接入知识库"
  fi
fi

# 5b. 和本机团队一致：管家规矩、管家设置随程序里的 _system/团队默认.json 自动补上；
#     这里再放行 kb 工具（~/.claude/settings.json 的 permissions.allow 加 mcp__kb，合并不覆盖）、
#     这台 claude 不支持 --permission-mode auto 时把后台会话改成 acceptEdits + 放行工具，并提示绕过知识库装的角色套件残留
FIX=auto; [ "${MCP}" = 1 ] && FIX=allow,auto,mcp
"${DIR}/kb" doctor --fix "${FIX}" 2>&1 | sed 's/^/    /' || warn "体检没跑完：装好后运行 kb doctor 看一下"
ok "体检完成（随时可以 kb doctor 或网页「⚙ 设置 → 🩺 体检」再看；✗ 角色套件残留去「设置 → 角色团 → 🗑 移除」）"

# 6. 账号（监听的不是本机地址时必须先有管理员账号，REQ-026）
case "${HOST}" in 127.0.0.1|localhost|::1) ;; *)
  if ! "${VENV}/bin/python" -c "import sys; sys.path.insert(0, '${DIR}/_system'); import kb; sys.exit(0 if kb.multi_user_mode() else 1)"; then
    echo "  网页要监听 ${HOST}（别的机器能访问），先建管理员账号："
    printf '  管理员用户名 [admin]：'; read -r ADMIN_NAME </dev/tty || ADMIN_NAME=""
    "${DIR}/kb" user add "${ADMIN_NAME:-admin}" --admin </dev/tty || die "没有建管理员账号：之后运行 kb user add <用户名> --admin"
  fi
  ok "已有登录账号（加成员：kb user add <用户名>，或网页「⚙ 设置 → 👥 成员」）" ;;
esac

# 6b. 网页端口：--port 写进 _system/web.json（以后 kb web / 开机自启都按它；启动后可在网页「⚙ 设置 → 🌐 网页端口」改）
if [ "${PORT_SET}" = 1 ]; then
  "${DIR}/kb" port set "${PORT}" >/dev/null && ok "网页端口：${PORT}（改：kb port set <端口>）" || die "端口 ${PORT} 不对：要在 1024–65535 之间"
else
  PORT=$("${VENV}/bin/python" -c "import sys; sys.path.insert(0, '${DIR}/_system'); from kblib.infra import webport; print(webport.configured()[0])" 2>/dev/null || echo 11555)
fi

# 7. 开机自动启动网页（可选）
if [ "${AUTOSTART}" = 1 ]; then
  ENVPATH="$(dirname "${CLAUDE:-/usr/bin/true}"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
  if [ "${OS}" = Darwin ]; then
    PL="${HOME}/Library/LaunchAgents/com.local.kb.web.plist"
    mkdir -p "$(dirname "${PL}")"
    cat > "${PL}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.local.kb.web</string>
  <key>ProgramArguments</key><array><string>${DIR}/kb</string><string>web</string><string>--host</string><string>${HOST}</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>${ENVPATH}</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${DIR}/_system/web.log</string>
  <key>StandardErrorPath</key><string>${DIR}/_system/web.log</string>
</dict></plist>
PLIST
    launchctl bootout "gui/$(id -u)" "${PL}" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$(id -u)" "${PL}" && ok "网页已设为登录后自动启动（http://${HOST}:${PORT}）"
  else
    UNIT="${HOME}/.config/systemd/user/kb-web.service"
    mkdir -p "$(dirname "${UNIT}")"
    cat > "${UNIT}" <<UNITF
[Unit]
Description=知识库网页
After=network-online.target

[Service]
ExecStart=${DIR}/kb web --host ${HOST}
Environment=PATH=${ENVPATH}
Restart=always
RestartSec=5
StandardOutput=append:${DIR}/_system/web.log
StandardError=append:${DIR}/_system/web.log

[Install]
WantedBy=default.target
UNITF
    systemctl --user daemon-reload && systemctl --user enable --now kb-web.service && ok "网页已设为开机自动启动（http://${HOST}:${PORT}）"
    loginctl show-user "$(id -un)" -p Linger 2>/dev/null | grep -q yes || warn "没退出登录也要一直跑：sudo loginctl enable-linger $(id -un)"
  fi
fi

# 8. 其他检查
command -v git >/dev/null && ok "git 可用" || warn "没有 git：代码仓库索引、开发分支识别不可用（macOS：xcode-select --install；Linux：apt install git）"

# 9. 装在本机：启动网页并打开浏览器，首页有「三步上手」
URL="http://127.0.0.1:${PORT}"
case "${HOST}" in 127.0.0.1|localhost) LOCAL=1 ;; *) LOCAL=0 ;; esac
if [ "${OPEN}" = 1 ] && [ "${LOCAL}" = 1 ] && [ -t 1 ]; then
  if ! curl -s -o /dev/null "${URL}/" 2>/dev/null; then
    nohup "${DIR}/kb" web >> "${DIR}/_system/web.log" 2>&1 &
    sleep 2
    # 端口被别的程序占了会自动顺延：按实际端口打开
    REAL=$("${VENV}/bin/python" -c "import sys; sys.path.insert(0, '${DIR}/_system'); from kblib.infra import webport; print(webport.actual().get('端口') or '')" 2>/dev/null)
    [ -n "${REAL}" ] && [ "${REAL}" != "${PORT}" ] && { warn "端口 ${PORT} 被占用，网页改用 ${REAL}（固定换端口：kb port set <端口>）"; PORT=${REAL}; URL="http://127.0.0.1:${PORT}"; }
    for _ in 1 2 3 4 5 6 7 8 9 10; do curl -s -o /dev/null "${URL}/" 2>/dev/null && break; sleep 1; done
  fi
  if curl -s -o /dev/null "${URL}/" 2>/dev/null; then
    ok "网页已启动：${URL}"
    if [ "${OS}" = Darwin ]; then open "${URL}" >/dev/null 2>&1 || true; else command -v xdg-open >/dev/null && xdg-open "${URL}" >/dev/null 2>&1 || true; fi
  fi
fi

cat <<TXT

安装完成。浏览器里的「👋 三步上手」会带你走一遍（不想自己准备，点「先看一个示例」）。
接下来：
  1. 新开一个终端（或 source ~/.zshrc），运行  kb  看总览
  2. kb web$( [ "${HOST}" = 127.0.0.1 ] || printf ' --host %s' "${HOST}" )  打开网页 http://${HOST}:${PORT}（「? 帮助」里有使用说明）
  3. 在这台机器的 Claude Code 里执行 /mcp，确认 kb 已连接
  4. 登录要抓取的系统：kb login <网址> <用户名>
  5. 部署在远端时，别的电脑上的 Claude Code 连这个知识库：
       在这台机器上  kb token add <名称>，按提示在那台电脑上运行 claude mcp add --transport http …
     放在公网建议前面加 HTTPS 反向代理（nginx / Caddy，保留 Host 头）
TXT
