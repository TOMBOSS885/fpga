# tasks/ — 示例题目

三道题，格式与评测题集一致，用来确认接口通畅与判定链路可用。

**它们不是自测题集。** 三道题量不出任何有意义的通过率，开发阶段请用
[VerilogEval v2](https://github.com/NVlabs/verilog-eval) 的 `dataset_spec-to-rtl`
（156 道题），用 `selftest/veval_import.py` 转成本目录的格式，并把实测结果写进
设计报告。

---

## 题目

| 题目               | 顶层        | 考察点                                             |
| ------------------ | ----------- | -------------------------------------------------- |
| `ex01_popcount8`   | `TopModule` | 纯组合逻辑、位宽                                   |
| `ex02_detect_1101` | `TopModule` | FSM、重叠检出、同步复位、输出时序差一拍            |
| `ex03_lfsr8`       | `TopModule` | 时序逻辑、控制信号优先级、反馈抽头                 |

每道题的测试台都刻意设了陷阱：

- `ex02` 的激励里有重叠序列 `1101101`（须检出两次）和一次发生在部分匹配途中的
  同步复位。用计数器而非移位寄存器实现的检测器通常能过前者、栽在后者。
- `ex03` 会同时拉高 `reset` 与 `load`。复位优先级写反的实现能正确运行几百拍，
  仅在少数样本上失败，实测为 229 个样本中错 5 个。

---

## 题面是英文

**评测题集的题面全部是英文**，取自 VerilogEval v2 的 `dataset_spec-to-rtl`。本目录
三道示例题已改写成与它逐格式对齐的英文，包括那句固定的开头、端口清单的缩进写法、
以及 `(8 bits)` 这种位宽标注：

```
I would like you to implement a module named TopModule with the following
interface. All input and output ports are one bit unless otherwise
specified.

 - input  clk
 - input  d   (8 bits)
 - output q   (8 bits)

The module should ...
```

**为什么不留中文版。** 示例题的作用是照着它开发。题面语言不一致的话，队伍在中文上
调好的提示词、解析逻辑与观察到的模型行为，评测当天面对的是另一种语言 —— token 分布
不同，模型表现也未必一样，而这种差异只会在评测当天暴露。

仓库的其余文档仍是中文，只有题面跟着评测题集走。

## 目录格式

```
ex03_lfsr8/
├── prompt.txt          题面。run.sh 读这个
├── ref.sv              RefModule 参考实现  ★ 不下发给智能体
├── tb.sv               参考测试台          ★ 不下发给智能体
├── top.txt             顶层模块名
├── task.json           判定元信息
└── reference/
    └── solution.sv     已知正确的 TopModule，仅用于验证判定链路
```

`task.json`：

```json
{
  "task_id": "ex03_lfsr8",
  "top": "TopModule",
  "part": "xczu3eg-sbva484-1-e",
  "period_ns": 5,
  "reference_module": "ref.sv",
  "testbench": "tb.sv",
  "tb_top": "tb",
  "extra_files": [],
  "reference": "reference/solution.sv"
}
```

测试台的最后一行必须是：

```
Mismatches: <n> in <m> samples
```

判定器只认这一行。格式与 VerilogEval v2 一致，因此两边的题目可以混用。

---

## 三件要注意的事

**一、`reference/` 不要喂给智能体。** 一个因为答案就在提示词里而通过的自测，什么都
没测到。它存在的唯一目的是证明判定链路能在已知正确的输入上走到 L3：

```bash
cd ../selftest && ./run_selftest.sh --reference
```

**二、`ref.sv` 与 `tb.sv` 是判定用的，智能体拿不到。** 正式评测时参考实现与测试台
都在赛事方手里，智能体只能拿到 `prompt` 与 `interface`。

因此不应将测试台接入智能体环内充作自我验证，此种做法会使本地得分虚高，至评测时
那条路径不存在。想在环内自我验证，只能自己写测试台。这是本赛道要解决的问题之一，
也是 L1 → L2 那 0.5 系数差的所在。

**三、模块名与端口是契约。** 参考测试台按具名端口例化被测模块：

```verilog
TopModule dut1 (.clk(clk), .reset(reset), .load(load), .data(data), .q(q_dut));
```

端口改名即等同于缺少一个端口，详细描述随即失败，该题计为 L0，其代价高于功能实现错误（后者仍有 L1 的
0.2 分）更贵。

---

## 自己加题

按上面的目录格式放进 `tasks/` 即可，`run_selftest.sh` 会自动发现所有含 `task.json`
的目录。

从 VerilogEval 转换用 `selftest/veval_import.py`，对应关系是直接的：

| VerilogEval              | 这里             |
| ------------------------ | ---------------- |
| `<name>_prompt.txt`      | `prompt.txt`     |
| `<name>_ref.sv`          | `ref.sv`（模块头由 `RefModule` 改名，仅用于判定，不下发） |
| `<name>_test.sv`         | `tb.sv`（需移除 `$dumpvars` 块，见 `selftest/README.md`） |

自己写测试台时，用 `ex01`/`ex02`/`ex03` 的模板即可：例化 `RefModule` 与 `TopModule`，
逐拍用 XOR 形式比对（`ref === (ref ^ dut ^ ref)`，参考实现里的 X 匹配任意值，
待测模块里的 X 不匹配），最后打印 `Mismatches: <n> in <m> samples`。
