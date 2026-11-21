# Agent image latency / Agent 单图交互耗时

The manual browser benchmark sends ordinary English and Chinese single-image requests on a fresh canvas and on a canvas with an imported image. Run both prompt sets. The browser checks that the current turn created exactly one real image node and one job, that the adopted preview has the stored asset's bytes, and that pre-existing nodes and assets are preserved.

手动浏览器基准在空画布和已有导入图片的画布上发送中英文单图请求。运行两组不同措辞。浏览器核对本轮只创建一个真实图片节点和一个任务，采纳后预览与保存的素材字节一致，已有节点和素材保留。

- Count samples within the [3-second interaction budget](ui-performance.md#interaction-budgets) from Send to the new node's first visible frame. Report the time from the same click to the decoded result appearing in the node separately.
- 按[交互预算](ui-performance.md#interaction-budgets)统计从点击发送到新节点首次可见不超过 3 秒的样本。成图耗时从同一次点击到已解码图片在节点内可见，单独报告。

Build with `npm run build` and install the browser dependencies described in the [validation guide](validation.md). Use an empty data directory and an existing workspace with the named model connections. Live runs consume model quota.

用 `npm run build` 构建，并按[验证指南](validation.md)准备浏览器依赖。使用空数据目录和包含指定模型配置的现有工作区。真实运行消耗模型额度。

```sh
OPENDIRECTOR_ROOT="$(mktemp -d)" \
  PYTHONPATH="$PWD/packages/core/src:$PWD/apps/api/src" \
  python -m scripts.agent_image_benchmark_server \
  --model-root /path/to/configured/workspace \
  --agent-model MiniMax-M3.1-Flash --image-model MiniMax-H3-Image-Highspeed \
  --port 18261
```

```sh
OPENDIRECTOR_API_URL=http://127.0.0.1:18261 npm run preview -- --port 15261 --strictPort
```

```sh
export CREATION_API_URL=http://127.0.0.1:18261 CREATION_WEB_URL=http://127.0.0.1:15261
node tests/browser/agent_image_ordinary.mjs
AGENT_IMAGE_PARAPHRASES=1 node tests/browser/agent_image_ordinary.mjs
```

The runner emits raw samples and per-case summaries as JSON Lines. Check `withinBudget` alongside successful generation and the two duration metrics.

脚本以 JSON Lines 输出逐次记录和各场景汇总。结合生成成功数和两项耗时核对 `withinBudget`。
