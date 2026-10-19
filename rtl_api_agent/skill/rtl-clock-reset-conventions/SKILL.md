---
name: rtl-clock-reset-conventions
description: Rules and patterns for clocking and reset implementations in FPGA synthesis, preventing multi-driven nets, incorrect sensitivity lists, and simulation-synthesis mismatches.
---

# Agent Skill: RTL Clock and Reset Conventions

## Skill Metadata
- **Name:** `rtl-clock-reset-conventions`
- **Description:** 规范时钟边沿触发、同步/异步复位极性与敏感列表，防止时序仿真与综合行为不一致。
- **Trigger:** 时钟未驱动、复位极性反转、仿真多拍偏差、敏感列表不匹配。
- **Log signatures:** `multi-driven net`, `sensitivity list`, `asynchronous reset`, `uninitialized register`, `clock domain`, `Mismatches`

## 规则与模板

1. **时钟信号**：
   - 除非题面明确说明是纯组合逻辑（如 `This is purely combinational logic`），否则时序逻辑均在时钟上升沿触发：`always @(posedge clk)`。
2. **复位语义规范**：
   - **同步复位（默认，题面未提异步时均作同步）**：
     ```verilog
     always @(posedge clk) begin
         if (reset) begin
             q <= 8'h00;
         end else begin
             q <= d;
         end
     end
     ```
   - **异步复位（题面明确写明 asynchronous 时）**：
     ```verilog
     // 异步高有效复位
     always @(posedge clk or posedge reset) begin
         if (reset) begin
             q <= 8'h00;
         end else begin
             q <= d;
         end
     end

     // 异步低有效复位 (reset_n 或 rst_n)
     always @(posedge clk or negedge reset_n) begin
         if (!reset_n) begin
             q <= 8'h00;
         end else begin
             q <= d;
         end
     end
     ```
3. **时钟使能与优先级**：
   - 复位优先级始终高于负载加载（`load`）或使能（`en`）。
