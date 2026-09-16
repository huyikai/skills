#!/usr/bin/env bash
# add-upstream.sh — 引入/刷新第三方开源 skill
#
# 第三方 skill 与自研平级放在 skills/<name>/ 下，SKILL.md 等文件保留上游原样；
# 每个目录自动生成 UPSTREAM.md（来源/版本/许可证），目录内无 LICENSE 时附带上游仓库根的 LICENSE。
# 来源登记在仓库根 upstream.tsv（name / repo / ref / path / commit / updated）。
# 本脚本只刷新文件，不做 git 提交；提交由人或 CI（.github/workflows/update-upstream.yml）完成。
#
# 用法:
#   add-upstream.sh add <git-url> <ref> <上游路径>...   引入新 skill（同名则覆盖更新，ref 为分支或 tag）
#   add-upstream.sh update                              按 upstream.tsv 刷新全部第三方 skill
#
# 示例:
#   add-upstream.sh add https://github.com/mattpocock/skills main skills/productivity/grill-me

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_DIR="$REPO_ROOT/skills"
TSV="$REPO_ROOT/upstream.tsv"
TSV_HEADER='# name	repo	ref	path	commit	updated'

usage() {
  sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

die() { echo "错误: $*" >&2; exit 1; }

# 展示用 URL：git@host:owner/repo 转 https://host/owner/repo，其余形式原样返回
display_url() {
  local u="$1"
  case "$u" in
    git@*:*)
      u="${u#git@}"
      u="https://${u/:/\/}"
      ;;
  esac
  echo "$u"
}

# 从 LICENSE 文本粗判许可证类型，识别不出返回空串
license_name() {
  local head_line
  head_line="$(head -n 5 "$1" 2>/dev/null || true)"
  case "$head_line" in
    *MIT*) echo "MIT" ;;
    *Apache*) echo "Apache-2.0" ;;
    *BSD*) echo "BSD" ;;
    *ISC*) echo "ISC" ;;
    *) echo "" ;;
  esac
}

# 新增/更新一行登记（按 name 去重，其余列整体替换）
tsv_upsert() {
  local name="$1" repo="$2" ref="$3" path="$4" commit="$5" today="$6" tmpf
  [ -f "$TSV" ] || printf '%s\n' "$TSV_HEADER" > "$TSV"
  tmpf="$(mktemp)"
  {
    head -n 1 "$TSV"
    tail -n +2 "$TSV" | awk -F'\t' -v n="$name" 'NF && $1 != n'
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$repo" "$ref" "$path" "$commit" "$today"
  } > "$tmpf"
  mv "$tmpf" "$TSV"
}

# 把上游 skill 目录落位为 skills/$name/，生成 UPSTREAM.md，按需附带上游 LICENSE
refresh_one() {
  local name="$1" src="$2" repo_top="$3" repo="$4" ref="$5" path="$6" commit="$7" today="$8"
  local dest="$SKILLS_DIR/$name" license="" license_line

  [ -f "$src/SKILL.md" ] || die "上游不存在 skill: $path"

  rm -rf "$dest"
  cp -R "$src" "$dest"
  rm -rf "$dest/.git"

  if [ -f "$dest/LICENSE" ]; then
    license="$(license_name "$dest/LICENSE")"
  elif [ -f "$repo_top/LICENSE" ]; then
    cp "$repo_top/LICENSE" "$dest/LICENSE"
    license="$(license_name "$dest/LICENSE")"
  fi
  if [ -n "$license" ]; then
    license_line="${license}（见本目录 LICENSE）"
  else
    license_line="见上游仓库 LICENSE"
  fi

  cat > "$dest/UPSTREAM.md" <<EOF
# Upstream

- 来源: $(display_url "$repo")
- 上游路径: $path
- 版本: $ref @ ${commit:0:12}
- 许可证: $license_line
- 更新日期: $today
- 本地改动: 无
EOF

  tsv_upsert "$name" "$repo" "$ref" "$path" "$commit" "$today"
  echo "已同步: $name ($ref @ ${commit:0:12})"
}

cmd_add() {
  [ $# -ge 3 ] || usage 1
  local repo="$1" ref="$2"
  shift 2

  # 全局变量而非 local：EXIT trap 触发时函数局部变量已出作用域
  tmp_top="$(mktemp -d)"
  trap 'rm -rf "$tmp_top"' EXIT

  echo "==> clone $repo @ $ref"
  git clone --quiet --depth 1 --branch "$ref" "$repo" "$tmp_top/src" 2>/dev/null \
    || die "clone 失败: $repo @ ${ref}（ref 须为分支或 tag）"
  local commit
  commit="$(git -C "$tmp_top/src" rev-parse HEAD)"
  local today
  today="$(date +%F)"

  local p name
  for p in "$@"; do
    p="${p%/}"
    name="$(basename "$p")"
    refresh_one "$name" "$tmp_top/src/$p" "$tmp_top/src" "$repo" "$ref" "$p" "$commit" "$today"
  done
  echo "==> 登记文件: $TSV"
}

cmd_update() {
  [ -f "$TSV" ] || die "未找到 ${TSV}，先用 add 引入第三方 skill"
  local combos
  combos="$(awk -F'\t' '!/^#/ && NF {print $2 "\t" $3}' "$TSV" | sort -u)"
  [ -n "$combos" ] || die "upstream.tsv 没有登记任何 skill"

  tmp_top="$(mktemp -d)"
  trap 'rm -rf "$tmp_top"' EXIT

  local refreshed=0 skipped=0 repo ref name path
  while IFS=$'\t' read -r repo ref; do
    echo "==> clone $repo @ $ref"
    if ! git clone --quiet --depth 1 --branch "$ref" "$repo" "$tmp_top/src" 2>/dev/null; then
      echo "警告: clone 失败，跳过 $repo @ $ref" >&2
      continue
    fi
    local commit
    commit="$(git -C "$tmp_top/src" rev-parse HEAD)"
    local today
    today="$(date +%F)"
    while IFS=$'\t' read -r name path; do
      if [ -f "$tmp_top/src/$path/SKILL.md" ]; then
        refresh_one "$name" "$tmp_top/src/$path" "$tmp_top/src" "$repo" "$ref" "$path" "$commit" "$today"
        refreshed=$((refreshed + 1))
      else
        echo "警告: 上游已不存在 ${path}，保留本地 ${name} 不动" >&2
        skipped=$((skipped + 1))
      fi
    done <<< "$(awk -F'\t' -v r="$repo" -v f="$ref" '!/^#/ && NF && $2 == r && $3 == f {print $1 "\t" $4}' "$TSV")"
  done <<< "$combos"
  echo "==> 完成: 刷新 $refreshed 个，跳过 $skipped 个"
}

case "${1:-}" in
  add)    shift; cmd_add "$@" ;;
  update) shift; cmd_update "$@" ;;
  -h|--help|"") usage ;;
  *) die "未知命令: ${1}（可用: add / update）" ;;
esac
