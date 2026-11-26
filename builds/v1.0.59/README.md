# Monster-ww

基于图像识别的《鸣潮》自动化工具便携版，支持后台运行。仅通过 Windows 接口模拟用户操作，不读取内存、不修改游戏文件。

> 使用第三方辅助工具可能导致账号被封禁，请自行评估风险。本项目仅供个人学习与交流，请勿用于商业用途。

## 便携包使用

解压后双击 `Monster-ww/Monster-ww.exe`；需要命令行参数或完全离线启动时使用 `Monster-ww/Monster-ww.cmd`。
两个入口都会请求管理员权限：游戏以管理员权限运行时，未提权进程的 `PostMessage` 会被 UIPI 拦截（错误 5），表现为点击和按键完全无反应。

`updater` 模式下，启动器从本仓库发布的版本 tag 拉取增量更新，只传输变化的源码文件；Python 环境、`configs/`、日志和自定义脚本都会保留。
想完全跳过更新检查，可改用 `Monster-ww.cmd`（设置了 `PYAPPIFY_UPGRADEABLE=0`）。

## 队伍创意工坊（离线）

`workshop_bundle/` 内置 49 个队伍、69 个共享配置（约 3 MB）。「队伍配置」页里浏览和导入工坊配置全部读取本地文件，不访问任何外部 CDN；
也可以继续用「导入队伍」导入本地 ZIP。索引在 `workshop_bundle/teams.json`，配置包在 `workshop_bundle/codes/`。

## 自定义脚本目录

自定义任务与导入脚本统一使用框架规范目录 `ok_tasks/` 和 `ok_import/`，模板编辑与脚本打包共用同一位置。

从 v19–v22 升级时，旧的 `Monster-ww_scripts/`、`Monster-ww_drop/` 会在启动时自动迁移过去：
先做全量冲突预检，**同名但内容不同的文件会中止启动并提示手动处理**，绝不覆盖用户文件；内容相同的文件会被合并清理。
遇到提示时请先备份两个目录，确认保留哪一份后再启动。

## 依赖与构建

运行时框架为自维护的 `Mon-script`（Python 包名 `Mon`），版本由 `requirements.txt` / `pyproject.toml` 精确锁定到框架仓库的发布 tag。
`requirements.txt` 内容变化时 PyAppify 会重新安装依赖，因此增量更新同样能升级内置框架。

发布由手动触发的 `Monster-ww Portable Release` 工作流完成，只产出便携 7z 压缩包（无安装器、无在线安装包）。
发布 tag 使用仅含运行时文件的精简 tree，包体积约 205 MB，同时保留增量更新能力。

更多说明见 `docs/zh-CN/index.md`。
