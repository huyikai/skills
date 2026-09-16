#!/usr/bin/env bash
# install.sh — 跨设备安装/同步本仓库的 skills 到本机各 agent 的 skill 目录
#
# 通用设计：不绑定任何特定 agent。目标目录由下方 TARGET_CANDIDATES 列出，
# 换了别的 agent 只需往列表里加一行它的 skill 目录。
#
# 用法:
#   install.sh list                        查看可用 skill、本机 skill 目录及安装状态
#   install.sh install <skill-name>        安装单个 skill（--target 指定目录）
#   install.sh install --all               安装全部 skill
#   install.sh update                      git pull 后刷新本机已安装的 skill
#
# 选项:
#   --target <dir>   指定安装目标目录（缺省时自动探测；探测到多个会提示选择）

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_DIR="$REPO_ROOT/skills"

# 各 agent 的全局 skill 目录候选，按需增删
TARGET_CANDIDATES=(
  "$HOME/.zcode/skills"
  "$HOME/.claude/skills"
  "$HOME/.agents/skills"
)

usage() {
  sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

die() { echo "错误: $*" >&2; exit 1; }

# 列出仓库中可安装的 skill（含 SKILL.md 的目录，跳过 _template）
repo_skills() {
  local d
  for d in "$SKILLS_DIR"/*/; do
    [ -f "$d/SKILL.md" ] || continue
    [ "$(basename "$d")" = "_template" ] && continue
    basename "$d"
  done
}

# 探测本机已存在的 skill 目录
detect_targets() {
  local d
  for d in "${TARGET_CANDIDATES[@]}"; do
    [ -d "$d" ] && echo "$d"
  done
}

# 选定目标目录：--target 优先；探测到 1 个直接用；多个交互选择；0 个报错
pick_target() {
  local target="${1:-}"
  local detected=() d
  while IFS= read -r d; do detected+=("$d"); done < <(detect_targets)

  if [ -n "$target" ]; then
    mkdir -p "$target"
    echo "$target"
    return
  fi

  if [ "${#detected[@]}" -eq 0 ]; then
    die "未探测到本机 skill 目录，请用 --target 指定。候选（本脚本认识但本机不存在）: ${TARGET_CANDIDATES[*]}"
  fi

  if [ "${#detected[@]}" -eq 1 ]; then
    echo "${detected[0]}"
    return
  fi

  echo "探测到多个 skill 目录，请选择安装目标:"
  local i=1
  for d in "${detected[@]}"; do echo "  $i) $d"; i=$((i + 1)); done
  read -r -p "输入编号 [1-${#detected[@]}]: " n
  if ! [[ "$n" =~ ^[0-9]+$ ]] || [ "$n" -lt 1 ] || [ "$n" -gt "${#detected[@]}" ]; then
    die "无效编号: $n"
  fi
  echo "${detected[$((n - 1))]}"
}

# 把仓库中的 skill 复制到目标目录（整目录替换，保持自包含）
install_one() {
  local name="$1" target="$2"
  [ -f "$SKILLS_DIR/$name/SKILL.md" ] || die "仓库中不存在 skill: $name"
  mkdir -p "$target"
  rm -rf "$target/$name"
  cp -R "$SKILLS_DIR/$name" "$target/$name"
  echo "已安装: $name -> $target/$name"
}

cmd_list() {
  echo "== 仓库可用 skill =="
  local skills=() s
  while IFS= read -r s; do skills+=("$s"); done < <(repo_skills)
  if [ "${#skills[@]}" -eq 0 ]; then
    echo "  （暂无，使用 skills/_template 创建）"
  else
    for s in "${skills[@]}"; do echo "  $s"; done
  fi

  echo
  echo "== 本机 skill 目录 =="
  local targets=() d
  while IFS= read -r d; do targets+=("$d"); done < <(detect_targets)
  if [ "${#targets[@]}" -eq 0 ]; then
    echo "  （未探测到，候选: ${TARGET_CANDIDATES[*]}）"
    return
  fi
  for d in "${targets[@]}"; do
    echo "  $d"
    if [ "${#skills[@]}" -gt 0 ]; then
      for s in "${skills[@]}"; do
        if [ -f "$d/$s/SKILL.md" ]; then
          echo "    [已安装] $s"
        fi
      done
    fi
  done
}

cmd_install() {
  local target="" names=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --target) target="$2"; shift 2 ;;
      --all)
        while IFS= read -r s; do names+=("$s"); done < <(repo_skills)
        shift
        ;;
      -h|--help) usage 0 ;;
      -*) die "未知选项: $1" ;;
      *) names+=("$1"); shift ;;
    esac
  done
  [ "${#names[@]}" -gt 0 ] || die "未指定要安装的 skill，用 <skill-name> 或 --all（查看可用: $0 list）"

  target="$(pick_target "$target")"
  local a
  for a in "${names[@]}"; do
    install_one "$a" "$target"
  done
}

cmd_update() {
  echo "==> git pull"
  git -C "$REPO_ROOT" pull --ff-only
  local targets=() d s refreshed=0
  while IFS= read -r d; do targets+=("$d"); done < <(detect_targets)
  [ "${#targets[@]}" -gt 0 ] || { echo "本机无 skill 目录，无需刷新。"; return; }
  while IFS= read -r s; do
    for d in "${targets[@]}"; do
      if [ -f "$d/$s/SKILL.md" ]; then
        rm -rf "$d/$s"
        cp -R "$SKILLS_DIR/$s" "$d/$s"
        echo "已刷新: $s @ $d"
        refreshed=$((refreshed + 1))
      fi
    done
  done < <(repo_skills)
  echo "完成，共刷新 $refreshed 处。"
}

case "${1:-}" in
  list)    shift; cmd_list "$@" ;;
  install) shift; cmd_install "$@" ;;
  update)  shift; cmd_update "$@" ;;
  -h|--help|"" ) usage ;;
  *) die "未知命令: $1（可用: list / install / update）" ;;
esac
