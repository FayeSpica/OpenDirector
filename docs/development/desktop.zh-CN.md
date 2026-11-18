# 桌面应用

[English](desktop.md)

桌面应用复用 Web UI 与 Python 业务服务。Electron 通过私有 JSON 行控制管道启动一个 Python Host，由系统分配本地端口。每次启动生成独立凭证，仅主进程转发带鉴权的请求。渲染进程使用 `opendirector://app`，没有 Node 权限，仅通过有限 IPC 登记自身编辑会话及同步语言和导出状态。

应用资源只读。Electron 用户数据目录保存 `workspace/`（工程和配置）、`logs/` 与语言缓存，不搬迁或覆盖已有源码、pip 工作区。文件输入使用原生选择器，下载使用原生保存对话框。关闭窗口会隐藏窗口并保留导出；退出前检查 Agent、生成、工具和浏览器导出任务，说明中断影响后停止 Host，并仅释放本窗口登记的编辑会话；父控制管道断开时也执行相同清理。远端任务可能继续执行并产生费用，停止应用不代表已取消远端任务。

## 构建与检查

在目标系统和架构上构建，需要 `.nvmrc` 指定的 Node.js、npm、uv 和独立 FFmpeg/ffprobe 二进制。最终用户无需准备这些开发工具。

```bash
npm ci --prefix apps/desktop
npm test --prefix apps/desktop
OPENDIRECTOR_FFMPEG_DIR=/absolute/path/to/standalone-media npm run prepare:runtime --prefix apps/desktop
npm run pack --prefix apps/desktop
node apps/desktop/scripts/smoke.mjs /absolute/path/to/packaged/OpenDirector
```

运行时准备复用现有构建后端生成 Python wheel，包含前端、技能和实际 OpenCode 可执行文件；将 wheel 和依赖安装到私有独立 Python，验证 CLI 帮助及两个媒体程序，并将平台写入 `runtime/manifest.json`。不会修改系统 Python。提供对应平台可分发的媒体程序及许可证；依赖包管理器外部动态库的系统二进制不能直接作为便携运行时。

冒烟检查使用临时数据目录启动实际打包程序，进入画布，检查鉴权健康接口和工程查询，验证渲染隔离及中英文切换，隐藏窗口并确认 Host 保留，退出后重开同一工程，不调用模型服务。该检查不代表已完成 Agent 辅助创作、导出质量、签名或未测试平台的验收。

## 终端命令

通过应用菜单的“管理终端命令”在 `~/.local/bin` 安装启动器，必要时将该目录加入 PATH。应用会保留已有入口。如需卸载或修复旧安装，请检查应用显示的路径，确认入口属于 OpenDirector 后手动移除。修复时再点一次安装。`opendirector desktop` 打开此安装的桌面应用；`opendirector serve` 运行独立 API。业务命令沿用 `--url`、工程、会话和版本规则，目前不支持外部 CLI 自动连接桌面私有 Host。

## 交付边界

本模块对应 #363 的共用应用。平台安装、签名和分发由 #364–#366 负责，自动更新由 #367 负责。浏览器导出仍需应用进程参与，退出应用后不能在后台完成；首版壳未开放麦克风权限。

发布前须按 #363 在各声明平台完成干净环境验收：Agent 辅助创作与工具调用、编辑、导出、重开、故障恢复、重复启动和工程锁。冒烟通过不能替代完整验收；操作系统、架构和签名状态随平台验收证据记录。

## 发布安装包

桌面发布工作流构建未签名的 Apple Silicon DMG 和 Windows x64 NSIS 安装包。两端在原生运行器验证通过后，将安装包和 SHA-256 校验文件上传到同一个草稿 Release。涉及桌面打包的 PR 也会运行 Windows 安装验证。
