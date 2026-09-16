# skills

> Reusable agent skills for AI coding agents — writing, video, design, workflow.

个人 agent skills 管理仓库：跨设备同步、一键安装，通用格式，不绑定任何特定 agent。

## 目录结构

```
skills/              所有 skill，一个 skill 一个文件夹，内含 SKILL.md
└── _template/       新建 skill 时复制此模板
scripts/
├── install.sh       跨设备检测与安装脚本
└── add-upstream.sh  第三方 skill 引入与刷新
upstream.tsv         第三方 skill 来源登记（name / repo / ref / path / commit / updated）
.github/workflows/   每周自动刷新第三方 skill
```

## 快速开始（新设备）

```bash
git clone https://github.com/huyikai/skills && cd skills

# 查看本机已有哪些 agent 的 skill 目录、各 skill 的安装状态
./scripts/install.sh list

# 安装全部 / 单个（不指定 --target 时自动探测本机 skill 目录，多目标会提示选择）
./scripts/install.sh install --all
./scripts/install.sh install <skill-name>

# 拉取最新并刷新本机已安装的 skill
./scripts/install.sh update
```

## Skills 索引

| Skill | 分类 | 来源 | 说明 |
|-------|------|------|------|
| quota-watch | workflow | 自研 | 巡检 AI API 订阅额度与余额，按周理想消耗曲线判定用量偏快偏慢，支持邮件与 macOS 通知 |
| grill-me | productivity | [mattpocock/skills](https://github.com/mattpocock/skills) | 拷问式访谈压测计划/设计，`/grill-me` 手动触发；依赖 grilling |
| grilling | productivity | [mattpocock/skills](https://github.com/mattpocock/skills) | 同 grill-me，模型可主动调用 |
| handoff | productivity | [mattpocock/skills](https://github.com/mattpocock/skills) | 把当前对话压缩成交接文档，便于另一个 agent 接手 |
| wait-what | productivity | [mattpocock/skills](https://github.com/mattpocock/skills) | 没听懂某条消息时，用你缺失的上下文重新解释 |
<!-- 新增 skill 后在此添加一行，例如：
| my-skill | writing | 自研 | 一句话说明用途和触发场景 |
-->

## 新增 skill

1. 复制模板：`cp -r skills/_template skills/<your-skill>`
2. 修改 frontmatter 的 `name` / `description`（`description` 决定 agent 何时使用该 skill，务必写清触发场景）
3. 在上方索引表添加一行
4. Commit & push，其他设备执行 `./scripts/install.sh update` 即可同步

## 第三方 skill

他人开源的 skill 与自研平级放在 `skills/` 下，由脚本统一引入、登记与刷新：

```bash
# 引入（可一次多个上游路径；ref 为上游分支或 tag，同名重跑即覆盖更新）
./scripts/add-upstream.sh add <git-url> <ref> <上游路径>...

# 手动刷新全部第三方 skill 到上游最新（只改文件不提交，review 后自行 commit）
./scripts/add-upstream.sh update
```

- 来源与版本登记在仓库根 [`upstream.tsv`](./upstream.tsv)；每个第三方 skill 目录内附 `UPSTREAM.md`（来源、版本、许可证）与上游 LICENSE 副本，保证目录自包含
- SKILL.md 保留上游原样（可能含 `disable-model-invocation` 等私有字段）；如做本地修改，记入该目录 `UPSTREAM.md` 的「本地改动」
- 只引入 MIT 等宽松许可证的项目

### 每周自动更新

`.github/workflows/update-upstream.yml` 每周一 06:00（北京时间）自动执行 `add-upstream.sh update`，有变化则以 `github-actions[bot]` 身份提交并推送；各设备 `./scripts/install.sh update` 同步。也可在仓库 Actions 页手动触发（Run workflow）。每次更新为一条独立 commit，`git log` 可审计可回滚。注意：GitHub 定时任务可能延迟数分钟；仓库连续 60 天无活动时定时任务会被自动停用。

## 约定

- 自研 skill 的 SKILL.md 只使用 `name`、`description` 两个标准 frontmatter 字段，不使用任何 agent 私有语法；第三方 skill 保留上游原样
- 安装方式为整目录复制（非软链），自包含、跨设备安全
- 分类信息记录在上面的索引表中，不体现在目录结构上

## License

[MIT](./LICENSE)
