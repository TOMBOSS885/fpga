# RTL track 测试样例库

本目录只存放用于 **RTL 赛道开发自测** 的公开样例，不属于正式评测题集，也不参与参赛提交的 agent 运行时输入。

## 当前样例

- `official/verilog-eval-v2/dataset_spec-to-rtl/`：来自 NVIDIA 官方 `NVlabs/verilog-eval` 仓库的 VerilogEval v2 specification-to-RTL 数据集。
- 每道题保留官方的 `*_prompt.txt`、`*_ref.sv` 和 `*_test.sv` 文件。
- `dataset_code-complete-iccad2023` 未下载，因为它是代码补全任务，不是本比赛的 RTL specification-to-RTL 开发样例。

## 官方来源

- 上游仓库：https://github.com/NVlabs/verilog-eval
- RTL 数据集：https://github.com/NVlabs/verilog-eval/tree/main/dataset_spec-to-rtl
- 本次下载版本：`c498220d0a52248f8e3fdffe279075215bde2da6`
- 上游许可证：见 `official/verilog-eval-v2/LICENSE`（MIT；同时保留上游 README 及其声明）。

## 与比赛的关系

比赛仓库的 RTL 评分说明将 VerilogEval v2 `dataset_spec-to-rtl` 作为开发阶段自测来源。该公开数据集只能用于开发、回归和工具链检查；正式评测题集由赛事方在评测窗口提供，不能把这里的题目当作正式隐藏题集，也不能把参考实现直接注入 agent 的题面或模型提示词。
