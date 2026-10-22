---
name: rtl-control-priority-pattern
description: Design patterns for strict control signal priority hierarchies (Reset > Clear > Load > Enable) in sequential RTL.
---

# Agent Skill: RTL Control Signal Priority Pattern

## Skill Metadata
- **Name:** `rtl-control-priority-pattern`
- **Description:** 确保时序逻辑中多个控制信号的优先级严格符合规格要求（如 reset > clear > load > enable），杜绝控制信号竞争与状态污染。
- **Trigger:** 优先级竞争、load/enable 在 reset 有效时误动作、LFSR 种子加载冲突、多分支条件重叠。
- **Log signatures:** `priority`, `load`, `enable`, `clear`, `reset priority`, `control hazard`, `Mismatches`

## 规则与核心范式

1. **级联优先阶梯（Cascaded Priority Ladder）**：
   - 永远使用显式级联的 `if ... else if ... else` 结构表达优先级，禁止使用多个平行独立的 `if`：
     ```verilog
     always @(posedge clk) begin
         if (reset) begin
             // 最高优先级：同步复位
             q <= RESET_VAL;
         end else if (clear) begin
             // 次高优先级：同步清零
             q <= '0;
         end else if (load) begin
             // 中优先级：并行数据载入
             q <= data_in;
         end else if (enable) begin
             // 低优先级：正常计数/移位/运算
             q <= next_val;
         end
         // 隐式保持：未使能时锁存当前值，无需显式写 q <= q
     end
     ```
2. **复位与载入同时有效测试陷阱**：
   - 评测测试台必然构造 `reset == 1 && load == 1` 的对抗样本。若 `load` 的判断先于 `reset` 或与之平行，测试台将直接报错。
   - 必须确保 `if (reset)` 位于最外层或最高判定分支。
3. **带使能的寄存器输出（Registered Output with Enable）**：
   - 保证使能信号为低电平时，输出寄存器保持上一拍稳定值，不可产生高阻态或意外翻转。
