# 独立评测与本地真实实验模块

基于队友 `tombossking_test` 的提交 `868b44dc1e0ffb0e951394462978e4fe66566eb9`，包含可靠性修复、外部判定器、可重建测试题、小样本真实对照及失败分析。不是独立重做agent，也不是正式比赛镜像。

## 已实际运行

- 官方公开3题：baseline/完整agent，共6次生成。
- 已知开发回归6题：baseline/完整agent，共12次生成。
- 公开3题关闭skill消融：3次生成。总计21次真实模型生成流程，不是21道不同题。
- 36项回归测试通过，包括3项原生Vivado测试。
- 6个测试台正反验收：正确设计全部通过，恒0变异设计全部被检出。
- 原始公开产物出现部分综合异常，改用短EDA工作路径后，对全部公开产物复判；保留原结果和复判记录，没有更换模型生成答案。

详细结果在 `docs/evaluation_report_20261006.md`，结构化统计在 `experiments/evaluation_summary_20261006/summary.json`。

## 实际开发配置（与团队14B部署声明分开）

- 已安装的 Ollama 0.35.1，模型 `qwen2.5-coder:7b-instruct-q4_K_M`。
- 模型digest：`dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364`。
- 服务context为16384；原始服务metadata及前后加载信息保留在results.json。
- 本机NVIDIA RTX4060 Laptop 8GB、Vivado2026.1、ZU3EG、5ns。GPU加载快照不是峰值显存测量，也不是AMD合规证明。
- baseline原样使用官方example文件，temperature0、输出上限8192均未改；agent按现有角色代码设定请求参数，单次上限和温度见trace。
- 本轮agent deadline150秒、reserve20秒、max_rounds2、模型请求timeout45秒；外层生成timeout160秒。这是开发配置，不是赛前公告预算。
- 模型实验使用本机既有Python3.7及requests；工程回归测试另使用Python3.12。未安装或更新依赖。新环境应遵循项目requirements和适用Python版本重新验证。
- 模式顺序固定baseline后agent，不做模型并发采样。部分外部工具复查/测试存在后台重叠，时间仅用于诊断，不能据此比较官方代价分。

## 复现（已有本地模型服务）

从rtlagent2026目录运行，确保真实模型服务已启动、模型名与/v1/models一致：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_development_evaluation.ps1 -Suite public
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_development_evaluation.ps1 -Suite regression
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_development_evaluation.ps1 -Suite public -NoSkills
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_development_evaluation.ps1 -Suite regression -FixtureHealth
```

可用-Python、-Model、-BaseUrl、-VivadoBin参数覆盖本机路径。脚本不下载模型、不替你安装依赖、不启动外部云服务。每次使用新输出目录。

Linux可以直接设置环境变量调用evaluation/run_evaluation.py；仅启动方式不同，判定逻辑相同。AMD Linux环境本轮未实测。

## 还原日志证据

网页上传包为减少文件数量，将solution.v、trace.jsonl、generation.txt、EDA控制台及综合metrics打包为 `experiments/evaluation_bundle_20261006/evidence.jsonl`。每条含原相对路径、内容和SHA-256。

```sh
python -B scripts/unpack_evaluation_evidence.py
```

还原器校验摘要、限制路径在experiments内，遇到同名不同内容拒绝覆盖。结构化results.json独立保留。无需安装GitHub插件。

## 重建测试题

```sh
python -B scripts/build_regression_dataset.py --output /path/to/new_regression_dataset
```

固定seed20261005；参考预期在Python中计算，包含饱和加法、优先级计数器、enabled流水线及重叠检测。生成器使用原生Vivado验证正确实现及变异实现。目标目录必须不存在。

这些题已经参与开发，不能再称未见留出集。generator/positive_fixture/reference仅用于外部开发评测；不能作为skill答案库或输入agent。既有Dockerfile并不复制本模块。

## 边界

只提供输入文件隔离，没有操作系统级沙箱；不保证模型产生的自测参考正确。关闭skill的单次消融不能证明普遍提升。尚需扩大测试集、重复采样、独立未见题、AMD/ROCm/离线Docker/峰值显存/冷启动验证，以及原审查列出的接口和部署缺陷修复。现有REPORT.md中的14B和89分结论仍未核实。
