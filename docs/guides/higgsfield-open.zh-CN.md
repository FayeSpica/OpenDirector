# Higgsfield Open

在「设置 → 模型」中为图片或视频选择 **Higgsfield Open**，粘贴从 [Higgsfield Open](https://open.higgsfield.ai/api-keys) 复制的完整密钥（`KEY_ID:KEY_SECRET`）。接口地址为 `https://api.higgsfield.ai`。

- **Kling Image 4.0** 支持 2K 文生图和参考图编辑，最多可使用四张参考图。编辑参考图须为 PNG 或 JPEG，宽高均至少为 300 像素，宽高比介于 2:5 和 5:2 之间。
- **Kling 4.0** 支持生成 3–30 秒的 720p 或 1080p 视频。可以使用提示词、首帧图片，或明确指定首尾帧。使用图片帧时，将画幅设为自动以沿用图片画幅。

OpenDirector 为该服务接受单张不超过 10 MB 的 PNG 或 JPEG 参考图，并通过 Higgsfield 的签名上传流程传输。提示词中的图片引用编号对应上传顺序。

提交后，OpenDirector 保存远端请求 ID。恢复已受理的任务时会用该 ID 继续查询。任务在远端排队时可请求取消。参见 [Higgsfield Open 快速开始](https://open.higgsfield.ai/quick-start)。
