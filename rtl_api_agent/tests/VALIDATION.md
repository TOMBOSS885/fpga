# 本地验证记录

- 日期：2026-10-19。
- 环境：当前 Linux 开发机，Python 3.14.4；没有 Vivado、AMD 推理服务或 Docker 构建验证。
- 命令（在包根目录）：`python3 -B -m unittest discover -s tests -v`。
- 结果：43 项发现，41 项通过，2 项原生 Vivado 测试跳过，0 失败/错误；本次约 11.4 秒。
- HTTP 集成实际经过 `/v1/solve`、单题脚本、HTTP 模型 fixture 和假 EDA 可执行文件；假 EDA 不执行 RTL 语义检查。
- 挂起工具测试在 4 秒解题预算下保留初始 RTL checkpoint，并在测试要求的 5.2 秒内返回。
- `bash -n` 检查三个 shell 入口；Python AST 与 manifest JSON 检查通过。
- 官方 baseline 与原官方文件 SHA-256 相同：`537783e39db22079c33c19af475dbdb760a29ff1656a48c481d434131f84fb51`。
- 官方接口 skill 保持字节一致；原 `rtlagent2026`、全局 `skills` 没有工作区改动。

这份记录不能代表真实模型通过率、原生综合结果、AMD 后端兼容性或正式比赛成绩。启用 `RTL_NATIVE_TESTS=1` 后，如果工具缺失，原生测试应失败，不能把跳过项报告为通过。
