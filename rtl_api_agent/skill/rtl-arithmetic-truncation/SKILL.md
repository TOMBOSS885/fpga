---
name: rtl-arithmetic-truncation
description: Bit-width sizing, signed arithmetic extension, and population count rules to prevent Vivado truncation and overflow warnings.
---

# Agent Skill: RTL Arithmetic & Bit-Width Sizing

## Skill Metadata
- **Name:** `rtl-arithmetic-truncation`
- **Description:** 指导组合与时序算术逻辑的位宽规划、有符号数扩展、Popcount 累加树与溢出预防，杜绝 Vivado 截断告警与位宽溢出错误。
- **Trigger:** 计数溢出、Popcount 组合逻辑截断、有符号比较错乱、[Synth 8-3295] 告警。
- **Log signatures:** `truncation`, `overflow`, `popcount`, `bit count`, `carry`, `signed`, `Synth 8-3295`

## 规则与核心范式

1. **人口计数（Popcount / 1 的个数统计）标准范式**：
   - $N$ 位输入的 1 的个数最大值为 $N$，需要 $\lceil \log_2(N+1) \rceil$ 位位宽（例如 8 位输入需要 4 位输出，范围 0..8）：
     ```verilog
     // 纯组合逻辑无锁存器实现
     always @(*) begin
         out = 4'd0; // 必须第一行显式赋 0，防推断出 latch
         for (int i = 0; i < 8; i = i + 1) begin
             if (in[i]) out = out + 4'd1;
         end
     end
     ```
2. **算术扩展与溢出保护**：
   - 两个 $N$ 位无符号数相加，结果需为 $N+1$ 位。
   - 有符号数运算必须显式标注 `$signed(a) + $signed(b)`，且最高位正确符号扩展。
3. **位选择越界与隐式零填充**：
   - 显式书写位宽常量：优先使用 `4'd0` 或 `8'h01`，避免直接使用非定宽整数 `0` 或 `1`，防止综合器在隐式 32 位扩展时生成高位不匹配告警。
