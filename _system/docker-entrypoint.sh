#!/bin/sh
# 容器入口（控制面 / 执行机共用）：
#   web（默认）  首次启动按 KB_ADMIN_PASSWORD（默认 admin123）建管理员 admin（首次登录要改密码）；按 KB_BOOTSTRAP_RUNNERS 给同一个 compose 里的执行机
#               预发注册令牌（写到 /shared/<名称>.token，执行机第一次注册用掉后删除）；然后 kb web --host 0.0.0.0
#   runner      执行机：没注册过时用 KB_RUNNER_TOKEN，或等控制面预发的 /shared/<KB_RUNNER_NAME>.token
#   其他        原样交给 kb（如 user add / restore / doctor / migrate）
set -e
KB="python /kb/_system/kb.py"
mkdir -p "${KB_ROOT:-/data/kb}" "${HOME:-/data/home}"

case "${1:-web}" in
web)
  shift 2>/dev/null || true
  KB_ADMIN_PASSWORD="${KB_ADMIN_PASSWORD:-admin123}"
  if [ -n "$KB_ADMIN_PASSWORD" ] && ! $KB user list 2>/dev/null | grep -q '^- '; then
    $KB user add "${KB_ADMIN_USER:-admin}" --admin --password "$KB_ADMIN_PASSWORD"
  fi
  if ! $KB user list 2>/dev/null | grep -q '^- '; then
    echo "！还没有账号：网页要对别的机器开放必须先建管理员。" >&2
    echo "  在 .env 里设 KB_ADMIN_PASSWORD 后重启，或运行：docker compose exec control kb user add admin --admin" >&2
  fi
  if [ -n "$KB_BOOTSTRAP_RUNNERS" ] && [ -d /shared ]; then
    for n in $(echo "$KB_BOOTSTRAP_RUNNERS" | tr ',' ' '); do
      [ -s "/shared/$n.token" ] && continue
      [ -e "/shared/$n.registered" ] && continue
      $KB runner-reg-token "$n" > "/shared/$n.token.tmp" && mv "/shared/$n.token.tmp" "/shared/$n.token"
      chmod 600 "/shared/$n.token"
      echo "已给执行机 $n 预发注册令牌（只在本 compose 的共享卷里，用一次即作废）"
    done
  fi
  exec $KB web --host "${KB_HOST:-0.0.0.0}" --port "${KB_PORT:-11555}" "$@"
  ;;
runner)
  shift
  data="${KB_RUNNER_DATA:-/data}"
  if [ ! -s "$data/runner.json" ] && [ -z "$KB_RUNNER_TOKEN" ] && [ -n "$KB_RUNNER_NAME" ] && [ -d /shared ]; then
    i=0
    while [ ! -s "/shared/$KB_RUNNER_NAME.token" ] && [ $i -lt 120 ]; do i=$((i + 1)); sleep 1; done
    if [ -s "/shared/$KB_RUNNER_NAME.token" ]; then
      KB_RUNNER_TOKEN=$(cat "/shared/$KB_RUNNER_NAME.token"); export KB_RUNNER_TOKEN
      $KB runner --register-only "$@" && rm -f "/shared/$KB_RUNNER_NAME.token" && touch "/shared/$KB_RUNNER_NAME.registered"
      unset KB_RUNNER_TOKEN
    fi
  fi
  exec $KB runner "$@"
  ;;
*)
  exec $KB "$@"
  ;;
esac
