# 本地验证记录

- 更新日期：2026-10-22。
- 环境：当前 Linux 开发机，Python 3.14.4；Vivado 2026.1 位于 `/home/q/vivado26.1/2026.1/Vivado`，没有 AMD 推理服务或 Docker 构建验证。
- 命令（在包根目录）：`python3 -B -m unittest discover -s tests -v`。
- 结果：67 项发现，65 项通过，2 项原生 Vivado 测试跳过，0 失败/错误；本次 11.506 秒。沙箱内有 11 项因无法创建 loopback socket 而报权限错误，取得许可后在沙箱外完整重跑得到上述结果。
- 随后修订六个团队技能的规格优先规则及验证/修复提示；相关技能选择和工作流 13 项回归通过，七个技能的 `quick_validate.py` 校验通过。未通过文本/格式检查推断真实模型收益。
- Vivado 探测：`vivado v2026.1`、`xvlog`、`xelab` 可启动；器件数据库包含 `xczu3eg_sbva484`。Vivado batch 综合返回 42，日志为未找到有效许可证；Ubuntu 26.04 上 xsim 版本可启动，但最小仿真报告 `unexpected exception when evaluating tcl command`。
- 原生编译补查：显式 `always_latch`、常量 `initial` 配合下降沿寄存器、有符号扩展加法，三类临时设计均通过本包真实 `xvlog + xelab` 流程。临时工程已清理；此结果只证明这些样例可编译，不证明仿真行为或综合通过。
- HTTP 集成实际经过 `/v1/solve`、单题脚本、HTTP 模型 fixture 和假 EDA 可执行文件；假 EDA 不执行 RTL 语义检查。
- 挂起工具测试在 4 秒解题预算下保留初始 RTL checkpoint，并在测试要求的 5.2 秒内返回。
- 终端：80×24 实际 PTY 启动，完成滚动、编辑、保存和退出；自动测试覆盖 120×30、80×24、40×14 和小于推荐尺寸的窗口、最后一项编辑、部分 JSONL 行、轨迹替换与运行 nonce。
- `--doctor --no-ui` 实际发现四个 Vivado 2026.1 工具，版本探测通过，当前无 Vivado 相关进程；明确标注许可证/实际仿真/综合未验证。
- `bash -n` 检查四个 shell 入口；Python AST 与 manifest JSON 检查通过。
- 官方 baseline 与原官方文件 SHA-256 相同：`537783e39db22079c33c19af475dbdb760a29ff1656a48c481d434131f84fb51`。
- 官方接口 skill 保持字节一致；原 `rtlagent2026`、全局 `skills` 没有工作区改动。

这份记录不能代表真实模型通过率、原生综合结果、AMD 后端兼容性或正式比赛成绩。启用 `RTL_NATIVE_TESTS=1` 后，如果工具缺失，原生测试应失败，不能把跳过项报告为通过。
