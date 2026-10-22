# RTL track 测试样例库

本目录存放比赛推荐的公开 VerilogEval v2 specification-to-RTL 开发题。当前提供 **155 道可直接按官方目录格式调用的样例**，另有 **1 道上游接口矛盾题单独隔离**。这是公开开发题集，不能代表正式隐藏评测成绩。

原始的 156 道题已在本分支中下载；2026-10-22 使用比赛仓库原有的 `selftest/veval_import.py` 补齐可运行的目录格式，没有重新编写题面或参考算法。

## 目录与官方格式

```text
rtl_samples/
├── official/verilog-eval-v2/       保留的上游原件、版本及 SHA-256
├── tasks/                         正常题集，共 155 道
│   └── Prob009_popcount3/
│       ├── prompt.txt             英文题面，含 TopModule 端口清单
│       ├── ref.sv                 RefModule，仅供外部判定
│       ├── tb.sv                  外部判定测试台
│       ├── top.txt                TopModule
│       ├── task.json              官方自测脚本读取的元信息
│       └── reference/solution.sv  参考实现改名，仅用于验证判定链路
├── known_issues/                   端口矛盾的 Prob099_m2014_q6c
├── manifest.json                  来源、转换器摘要、题目路径与隔离原因
├── prepare_tasks.py               复用官方转换器，可离线生成和核对
├── run_selftest.py                兼容本分支 CRLF 换行的官方自测启动器
├── validation.json                本机原生编译抽查原始结果
├── VALIDATION.md                  验证范围与限制
└── LICENSE                        上游 MIT 许可证及原版权声明
```

每题使用官方默认器件 `xczu3eg-sbva484-1-e`、5 ns 时钟参数；**不生成 `interface.txt`**，接口从题面的端口清单读取，符合正式 RTL 输入形态。

`tb.sv` 仅按官方转换器移除写波形的 `$dumpfile/$dumpvars` 块，避免 Vivado 的前向引用错误。参考逻辑、比较规则和 `Mismatches: <n> in <m> samples` 输出格式保留。

## 直接使用

以下命令在仓库根目录执行。先校验原始文件和转换结果：

```bash
python3 -B rtl_samples/prepare_tasks.py --check
```

将任意一个 `tasks/Prob...` 目录作为智能体的输入目录。对实现官方接口的真实智能体，调用方式为：

```bash
export RTL_AGENT_DIR=/你的智能体代码所在目录
"$RTL_AGENT_DIR/run.sh" \
  /home/q/文档/fpga/rtl_samples/tasks/Prob009_popcount3 /tmp/rtl-popcount3
```

本样例分支不新增智能体入口，`RTL_AGENT_DIR` 指向实际保有 `run.sh` 的工作区。在终端面板中，把 `INPUT_DIR` 设为该题目录、`OUTPUT_DIR` 设为结果目录即可。输出应为 `solution.v` 和 `trace.jsonl`。模型调用方式由所用智能体的配置决定；官方示例的 mock 模式只检查调用链。

先验证官方自测脚本发现题目及工具路径：

```bash
python3 -B rtl_samples/run_selftest.py \
  --tasks rtl_samples/tasks --check
```

在 Vivado 环境和许可证正常后，用参考实现验证完整判定链路：

```bash
python3 -B rtl_samples/run_selftest.py \
  --tasks rtl_samples/tasks --reference --out /tmp/rtl-samples-reference
```

测试自己的方案时，给官方脚本添加 `--agent /你的智能体目录`，去掉 `--reference`，并设置 `--samples 5`。agent 和 baseline 应共用同一个真实模型后端；桩模式不能用于测模型收益。

`run_selftest.py` 原样转发官方 CLI 参数，使用原脚本定位 `env.sh`、判定器和方案入口。它只在内存中规范当前分支官方驱动脚本的 CRLF 换行，并保留原脚本的路径解析；官方文件没有修改，计分与判定逻辑没有重写。被调用的智能体 shell 入口和用户 `env.sh` 应使用 Linux LF 换行。

可先挑选以下样例逐题检查：

| 题目目录 | 内容 |
| --- | --- |
| `Prob009_popcount3` | 组合逻辑、置位数统计与输出位宽 |
| `Prob027_fadd` | 全加器与进位 |
| `Prob041_dff8r` | 同步复位寄存器 |
| `Prob047_dff8ar` | 异步复位寄存器 |
| `Prob086_lfsr5` | Galois LFSR、抽头与同步复位 |
| `Prob096_review2015_fsmseq` | 序列检测、状态与输出时序 |
| `Prob115_shift18` | 算术移位、加载与控制优先级 |
| `Prob146_fsm_serialdata` | 串行接收状态机与数据输出 |

## 隔离的上游问题

`Prob099_m2014_q6c` 的端口清单与 `RefModule` 声明 `Y1/Y3`，测试台却连接 `Y2/Y4`，题面正文也存在矛盾。本机已复现 `xelab` 的 `cannot find port` 错误。

该题按原样转换后保存在 `known_issues/`，没有擅自修题。把官方自测的 `--tasks` 指向 `rtl_samples/tasks` 时，只会发现正常题集的 155 道题。完整列表和隔离原因见 `manifest.json`。

## 来源与可复现性

- 上游仓库：https://github.com/NVlabs/verilog-eval
- 数据集：https://github.com/NVlabs/verilog-eval/tree/main/dataset_spec-to-rtl
- 固定版本：`c498220d0a52248f8e3fdffe279075215bde2da6`，原件于 2026-10-06 下载。
- 转换格式依据：`rtlagent2026/tasks/README.md`、`rtlagent2026/docs/API_CONTRACT.md` 和 `rtlagent2026/selftest/veval_import.py`。
- 许可证：MIT，保留 NVIDIA 和上游附带的 OpenAI 版权声明，见本目录 `LICENSE`。

`python3 -B rtl_samples/prepare_tasks.py` 可离线补齐生成文件；它先验证上游 SHA-256，并拒绝覆盖内容不同的已有文件。`--check` 只核对原件、官方转换输出和元信息，不运行模型或 EDA。需要 Python 3.10+。

`ref.sv`、`tb.sv`、`reference/` 只交给外部判定器；智能体只读取题面及正式协议允许的可选接口。不要把参考答案或测试台加进模型提示词或技能包。
