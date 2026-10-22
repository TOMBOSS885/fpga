---
name: rtl-arithmetic-truncation
description: Bit-width sizing, signed arithmetic extension, and population count rules to prevent Vivado truncation and overflow warnings.
---

# Arithmetic widths and signedness

接口位宽和溢出、截断、饱和规则由题面决定。不要为了消除告警扩大输出端口或把模运算改成饱和运算。

- **Trigger:** 计数溢出、Popcount 截断、有符号比较、乘法结果宽度、[Synth 8-3295] 告警。
- **Log signatures:** `truncation`, `overflow`, `popcount`, `bit count`, `carry`, `signed`, `Synth 8-3295`

- N 位 popcount 的完整结果需 `$clog2(N+1)` 位；使用足够宽的组合累加器并在每次求值时清零。若输出更窄，只按题面规定截断。
- N 位加法若需保留进位，在运算前零扩展到 N+1 位；有符号加法在运算前显式符号扩展。`$signed` 只改变有符号解释，不增加位宽。
- 拼接和位选结果可能变成 unsigned；混合 signed/unsigned 的比较、除法、右移尤其需要检查。算术右移使用 `>>>` 且左操作数必须具有正确的 signed 类型。
- 乘法使用足够宽的中间量，再明确选择规定的输出位段；不要依赖不清楚的上下文宽度传播。移位不能恢复已截断的高位。
- 计数器回卷、最大正数、最小负数、全 1、进位/借位与负数右移应列为自测边界。避免无尺寸常量意外引入 32 位 signed 传播。
