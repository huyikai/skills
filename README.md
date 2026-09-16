# skills

> Reusable agent skills for AI coding agents — writing, video, design, workflow.

个人 agent skills 管理仓库：跨设备同步、一键安装，通用格式，不绑定任何特定 agent。

## 目录结构

```
skills/              所有 skill，一个 skill 一个文件夹，内含 SKILL.md
└── _template/       新建 skill 时复制此模板
scripts/
└── install.sh       跨设备检测与安装脚本
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

| Skill | 分类 | 说明 |
|-------|------|------|
| quota-watch | workflow | 巡检 AI API 订阅额度与余额，按周理想消耗曲线判定用量偏快偏慢，支持邮件与 macOS 通知 |
<!-- 新增 skill 后在此添加一行，例如：
| my-skill | writing | 一句话说明用途和触发场景 |
-->

## 新增 skill

1. 复制模板：`cp -r skills/_template skills/<your-skill>`
2. 修改 frontmatter 的 `name` / `description`（`description` 决定 agent 何时使用该 skill，务必写清触发场景）
3. 在上方索引表添加一行
4. Commit & push，其他设备执行 `./scripts/install.sh update` 即可同步

## 约定

- SKILL.md 只使用 `name`、`description` 两个标准 frontmatter 字段，不使用任何 agent 私有语法
- 安装方式为整目录复制（非软链），自包含、跨设备安全
- 分类信息记录在上面的索引表中，不体现在目录结构上

## License

[MIT](./LICENSE)
