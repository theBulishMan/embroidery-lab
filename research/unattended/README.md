# 无人制版：固定样例验收

验收起点是用户选定的原稿、成品宽度、线材和底料。用程序从头跑到底，不挑结果、不改区块、不手调针向。透明 PNG 从上传后的分色开始；归档稿从指定版本开始。后者不是对照片首次 AI 修复效果的测试。

在项目根目录运行。把结果放到空目录，保留结果快照以便同一张图比较；系统还需要项目的 Ink/Stitch 运行环境和 Chrome。默认使用 macOS Chrome，其他位置可指定 `CHROME_BIN`。测试在本机顺序运行，不用模型、不向线上项目写数据。

```sh
qa_dir=$(mktemp -d)
.venv/bin/python research/unattended/bench.py . "$qa_dir" --core --holdout --validation --archived-first --acceptance --public --transfer --variants --audit
.venv/bin/python research/unattended/check-intake.py "$qa_dir/sail/input.png" "$qa_dir/service-smoke"
```

看每个配置的 `result.json`，也要查 `error.txt`（引擎出错、超时也要算入分母）。`trial` 是可以开始试绣，`adjust`、`blocked`、`preflight` 均不计通过。已导出的配置通过浏览器读取实际落针，分别检查露底、原色缺失或被遮盖、间隙是否被填死以及原有成组花纹是否丢失。即使正确拦截，也不能计作一次自动制版成功。

同一批样例一旦用于调整，就不再是新样本。继续改引擎时应保留这批回归，另外预先锁定新图、实际宽度、线材和底料。要评价生产能力，还需把未挑选的新设计用相同设置实绣，对照数字结果记录漏底、错色、轮廓错位与细纹辨识度；仅凭软件自评分不能认定可以量产。

公开图案来自 Twemoji v14.0.2。前十张用于版本对照。另六张在 v26 首次检验通用性，随后其中的咖啡杯和花朵用于定位镂空和成组填针问题。v27 单独保留了八张新图，在确定工艺规则后一次验收。格式补测用同一支铅笔验证 20 / 60 mm，并把三张图压成白底 JPG 验证分色。JPG 测试会明确指定保留或去除外连白色。不同尺寸、格式和归档版本单列。[样例署名](public-icons/ATTRIBUTION.md)
