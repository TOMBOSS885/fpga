# selftest/ — 本地分级判定

在本地跑一遍与赛事方口径一致的 L0–L3 判定，产出题集得分、pass@1 / pass@5 与增益。

```
L1 可编译  →  L2 仿真通过  →  L3 可综合
```

逐级递进：前一级不过，后面的级别一律不计。

---

## 用法

```bash
cp env.sh.example env.sh && vi env.sh      # 填自己的 Vivado 安装路径与 LLM 后端

./run_selftest.sh --check                  # 只做环境检查
./run_selftest.sh --reference              # 用参考实现验证判定链路（不需要模型）
./run_selftest.sh                          # 方案 + 基线，各 1 次采样
./run_selftest.sh --samples 5              # 正式评测所用的采样次数
./run_selftest.sh --no-baseline            # 跳过基线，不算增益
./run_selftest.sh --tasks ../tasks_veval   # 换一套题集
./run_selftest.sh --baseline               # 桩模式下也强制跑基线（见下）
```

**桩模式下基线默认被跳过。** `LLM_BACKEND=mock` 只对 `agent/llm.py` 有效，而
`baseline.py` 读的是 `LLM_BASE_URL`、不认这个变量 —— 桩模式下它照样会去连真的推理
服务。连得上就是拿真模型的基线去比 mock 的智能体，连不上就是分母为 0，两种情况算出
的增益都没有意义却看不出异常。所以桩模式 + agent 模式时自动跳过，配置行会打印
「基线 跳过（桩模式，增益不计）」。

`--baseline` 是给「确实起了真端点、只是没改 `LLM_BACKEND`」的人留的出路。

单独判一个文件：

```bash
python3 judge.py --task ../tasks/ex03_lfsr8 --solution /tmp/out/solution.v
# ex03_lfsr8           L3  CMS  coeff=1.0  28s  0/229 mismatch
```

`CMS` 是三级的通过情况（Compile / Match / Synth），未通过的位置显示 `-`。

---

## 需要什么

| 判定级别      | 用到的命令                                        |
| ------------- | ------------------------------------------------- |
| L1 可编译     | `xvlog --sv ...` 和 `xelab tb -s tbsim`           |
| L2 仿真通过   | `xsim tbsim -runall`                              |
| L3 可综合     | `vivado -mode batch`（`synth_design`）            |

**三级判定全部依赖 Vivado，没有替代路径。** 没装的话 `run_selftest.sh` 会明确报出
未检测到时仅进行结构检查，不会退回 iverilog 或 Verilator。赛事方全流程仅使用
AMD 工具链，本地自测退回别的仿真器会得到不一致的判定，那比没有判定更糟。

Vivado 的安装由队伍自行完成，本仓库不提供安装指导。判定不需要 GPU 亦不需要 AMD 显卡；
目标器件 `xczu3eg-sbva484-1-e` 属于免费档的器件支持范围，不需要专业版 license。

**没装 Vivado 仍然可以做的事：** 用 `LLM_BACKEND=mock` 跑 `example/run.sh`，验证输入
输出契约和 `trace.jsonl` 格式；`agent/tools.py::check_interface()` 也不需要 Vivado。

---

## 用 VerilogEval v2 自测

三道示例题量不出任何有意义的通过率。真正的开发期自测数据集是 VerilogEval v2 的
`dataset_spec-to-rtl`，156 道题。

```bash
git clone https://github.com/NVlabs/verilog-eval
python3 veval_import.py \
    --src verilog-eval/dataset_spec-to-rtl \
    --out ../tasks_veval
./run_selftest.sh --tasks ../tasks_veval --reference     # 先验判定链路
./run_selftest.sh --tasks ../tasks_veval --samples 5     # 再运行参赛方案
```

### 为什么需要转换

**VerilogEval 的官方 harness 依赖 iverilog**（`configure.ac` 里是硬性检查），
它的测试台在 Vivado 下直接编译不过。156 个 `_test.sv` 每一个都含有：

```verilog
initial begin
    $dumpfile("wave.vcd");
    $dumpvars(1, stim1.clk, tb_mismatch, ...);
end
```

而 `tb_mismatch` 在五行之后才声明。iverilog 接受这个前向引用，Vivado 的 `xvlog`
不接受：

```
ERROR: [VRFC 10-3380] identifier 'tb_mismatch' is used before its
                      declaration [..._test.sv:66]
ERROR: [VRFC 10-8530] module 'tb' is ignored due to previous errors
```

这个块只写波形转储，删掉不改变行为。**整个移植就是这一处改动。**

### 实测覆盖

在 Vivado 2026.1 上把 156 道题全部转换，并以 `RefModule` 改名为 `TopModule` 作为
已知正确的解逐题跑完 `xvlog` → `xelab` → `xsim`：

| 结果                                        | 题数    |
| ------------------------------------------- | ------: |
| 转换成功                                    | **156** |
| 三步全部 rc=0 且 `Mismatches: 0`            | **155** |
| 失败                                        | **1**   |

唯一失败的是 `Prob099_m2014_q6c`，**这是数据集自身的缺陷，不是转换的问题**：它的
`_prompt.txt` 与 `_ref.sv` 声明的输出端口是 `Y1` / `Y3`，而它的 `_test.sv` 按
`.Y2` / `.Y4` 例化，参考实现本身亦无法通过。

```
ERROR: [VRFC 10-3180] cannot find port 'Y4' on this module [test.sv:70]
```

任何解在这道题上都拿不到分。转换脚本照样输出它，不做特殊处理；这类题在统计时应当
剔除，否则会低估方案的真实水平。

### 本自测未覆盖的内容

转换后的题目用的是 VerilogEval 自己的测试台。评测题集不公开，它的测试台也不是这些。
**把这里的分数当作开发信号，不要当作预测。**

---

## 输出

```
selftest/out/
├── agent/<task>/s<k>/solution.v      每次采样的产物
│                    /trace.jsonl
│                    /run.log
├── baseline/...
├── logs/<task>.xvlog.log             工具原始日志
│     /<task>.xelab.log
│     /<task>.xsim.log
│     /<task>.synth.log
├── results/<mode>.<task>.s<k>.json   每个样本的判定结果
└── score.json                        汇总
```

单样本判定结果：

```json
{
  "task_id": "ex03_lfsr8",
  "level": 3,
  "coefficient": 1.0,
  "stages": {"compile": true, "simulate": true, "synth": true},
  "mismatches": 0,
  "samples": 229,
  "tool_error": null,
  "elapsed_s": 28.0
}
```

---

## 计分口径

```
题集得分 = Σ(题目系数) / 题数
能力得分 = 30 × 题集得分
增益     = 方案题集得分 / 基线题集得分
增益得分 = 40 × log(增益) / log(满分线倍数)
```

系数：L0 = 0、L1 = 0.2、L2 = 0.7、L3 = 1.0。

**pass@1 计分，pass@5 只作稳定性诊断。** pass@1 取每题各次采样系数的均值再对题目平均；
pass@5 取每题的最好一次。两者差值大，说明方案对采样运气的依赖高。

`--samples 1` 时 pass@1 与 pass@5 必然相等，这个数没有诊断意义。要看方差就用
`--samples 5`。

**本地无法自评的两项：** 代价（10 分）需要独占环境计时，工程质量（20 分）含人工评定。

增益满分线 `GAIN_FULL_MARK` 默认 2.5，**这是占位值，不是官方值**，正式取值于赛前公告。

---

## 一条使用建议

请先运行 `--reference`。该模式不依赖模型与 key，仅验证一项内容，即本地 Vivado 环境能否将
已知正确的代码判到 L3。** 这一步不过，后面所有分数都不可信。

三道参考实现在 Vivado 2026.1 / `xczu3eg-sbva484-1-e` 下实测均为 L3 CMS。
