---
name: rtl-shift-register-pattern
description: Idioms for sequence detection and Linear Feedback Shift Registers (LFSR), ensuring correct overlapping detection and non-zero initialization.
---

# Agent Skill: RTL Shift Register & Sequence Detector Pattern

## Skill Metadata
- **Name:** `rtl-shift-register-pattern`
- **Description:** 规范序列检测器与线性反馈移位寄存器（LFSR）设计，解决重叠检测漏报、移位方向错误、抽头异或位权反转与死锁陷阱。
- **Trigger:** 序列检测（如 1101）、重叠子串漏报、LFSR 周期缩水、抽头逻辑错误。
- **Log signatures:** `sequence`, `detector`, `lfsr`, `shift register`, `overlapping`, `feedback`, `taps`

## 规则与核心范式

1. **定长序列检测首选移位寄存器（Shift Register over FSM）**：
   - 对于固定长度（如 4 位 1101）的序列检测，移位寄存器方案优于多状态 FSM：
     ```verilog
     // 移位寄存器天然支持重叠检测（如 1101101 检出两次）且免疫 FSM 状态转移死锁
     reg [3:0] sr;
     always @(posedge clk) begin
         if (reset) begin
             sr       <= 4'b0000;
             detected <= 1'b0;
         end else begin
             sr       <= {sr[2:0], in};
             // 在输入入队的同一拍判定，并打拍输出
             detected <= ({sr[2:0], in} == 4'b1101);
         end
     end
     ```
2. **LFSR 反馈多项式与抽头方向**：
   - **Fibonacci LFSR**（左移，低位进抽头）：
     ```verilog
     // feedback 抽头由特定高位异或产生，填入最低位 [0]
     wire feedback = q[7] ^ q[5] ^ q[4] ^ q[3];
     q <= {q[6:0], feedback};
     ```
   - **防止全零/非法死锁**：
     - LFSR 在全零状态下异或反馈始终为 0，会陷入死锁循环。复位值必须赋予非零种子（如 `8'h01`）。
3. **输出对齐要求**：
   - 注意题面要求输出是纯组合还是打拍输出（registered output）。如果要求打拍输出，必须在 `always @(posedge clk)` 中赋值。
