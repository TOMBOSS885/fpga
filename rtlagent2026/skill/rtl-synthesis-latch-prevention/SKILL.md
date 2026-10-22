---
name: rtl-synthesis-latch-prevention
description: Techniques to detect and eliminate unintended latch inference and non-synthesizable constructs in Vivado synthesis.
---

# Agent Skill: RTL Synthesis Latch Prevention

## Skill Metadata
- **Name:** `rtl-synthesis-latch-prevention`
- **Description:** 防止 Vivado 综合推断出意外的锁存器（Latches），消除不可综合构造，确保从 L2 晋级 L3。
- **Trigger:** Vivado 报告推断锁存器、多驱动冲突、DRC 错误、不可综合语法。
- **Log signatures:** `Synth 8-327`, `inferring latch for variable`, `DRC`, `multi-driven net`, `Synth 8-6156`, `unsupported construct`

## 规则与修复要点

1. **组合逻辑锁存器根治法则**：
   - 只要使用 `always @(*)` 或 `always_comb`，必须在块的第一行给所有被赋值的变量赋予默认值：
     ```verilog
     always @(*) begin
         // 默认赋值，覆盖所有隐式分支
         out = 1'b0;
         status = 4'b0;
         
         if (enable) begin
             out = data_in;
         end
         // 即便没有 else，out 也不会变成 latch！
     end
     ```
   - 在 `case` 语句中，必须显式书写 `default:` 分支。
2. **禁止不可综合语法**：
   - 严禁使用浮点数 `real`、系统打印函数 `$display`（仅在调试测试台内允许，顶层模块必须剔除）、延时 `#10`、`fork...join`、`initial`。
3. **单驱动规则**：
   - 同一个 `reg`/`wire` 绝对不能在多个 `always` 块或多个 `assign` 语句中被赋值。
