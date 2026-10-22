# 样例验证记录

日期：2026-10-22。范围仅为当前样例分支中的公开开发题与格式转换。

## 原件和目录检查

执行 `python3 -B rtl_samples/prepare_tasks.py --check`：

- 原始文件按已有 `official/verilog-eval-v2/SHA256SUMS` 校验，156 道题的 prompt/ref/test 三件套完整。
- 转换直接调用未改动的 `rtlagent2026/selftest/veval_import.py`。
- 155 道题位于 `tasks/`，1 道已知矛盾题位于 `known_issues/`。
- 所有转换文件与官方转换器重新生成的结果逐字节一致，未生成 `interface.txt`。
- `task.json` 保留官方目录格式、TopModule、ZU3EG 器件与 5 ns 参数。
- 原始上游文件、官方转换器、原有三道示例题和智能体代码未修改。

当前分支官方 `run_selftest.sh` 使用 CRLF，直接执行会报 `env: bash\r: No such file or directory`。本样例目录增加 `run_selftest.py`，在内存中规范驱动脚本换行并保留其原始路径解析，未更改判定逻辑或磁盘上的官方脚本。使用 `python3 -B rtl_samples/run_selftest.py --tasks rtl_samples/tasks --check` 验证官方驱动的题目发现与工具路径；完整参考判定仍须等仿真/综合环境正常后执行。

实际为进程临时设置本机 Vivado `bin` 到 PATH 后，上述检查退出码为 0，官方驱动发现 155 道题和四个工具路径。`--check` 没有发送模型请求、运行分级判定或写入官方自测输出目录；日志显示的 mock 后端是本分支驱动默认配置，不能作为真实模型结果。

## 真实 Vivado 编译抽查

工具：`/home/q/vivado26.1/2026.1/Vivado/bin`，Vivado 2026.1。

每题在独立 `/tmp/rtl_sample_compile_*` 目录内运行，退出后清理工程：

```bash
xvlog --sv solution.sv ref.sv tb.sv
xelab tb -s fixture_check -timescale 1ps/1ps
```

这里的 `solution.sv` 来自该题的 `reference/solution.sv`，用于检查题目与测试台之间的编译连接。

| 题目 | xvlog + xelab |
| --- | --- |
| Prob009_popcount3 | 通过 |
| Prob027_fadd | 通过 |
| Prob041_dff8r | 通过 |
| Prob047_dff8ar | 通过 |
| Prob086_lfsr5 | 通过 |
| Prob096_review2015_fsmseq | 通过 |
| Prob115_shift18 | 通过 |
| Prob146_fsm_serialdata | 通过 |
| Prob099_m2014_q6c | 按预期复现缺少 Y2/Y4 端口的错误，已隔离 |

返回码、工具参数与耗时保存在 `validation.json`。

## 验证边界

本轮未对全部 155 道题逐题运行原生编译，未运行行为仿真、综合或真实模型解题。当前机器此前存在 xsim 运行异常和综合授权问题，本轮没有把编译抽查扩写成 L2/L3 成绩。

“加入正常题集”表示没有命中已知的端口矛盾且格式检查通过，不表示每道题的自然语言、参考实现和测试台语义都已经人工证明正确。这些公开题用于开发回归，不能用于预测正式隐藏评测得分。
