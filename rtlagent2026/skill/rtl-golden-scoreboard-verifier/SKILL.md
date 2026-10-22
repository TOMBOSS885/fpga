---
name: rtl-golden-scoreboard-verifier
description: Self-checking testbench architecture with behavioral golden models, race-condition free stimulus driving, and automated mismatch scoreboards.
---

# Agent Skill: RTL Golden Scoreboard Verifier

## Skill Metadata
- **Name:** `rtl-golden-scoreboard-verifier`
- **Description:** 自动构建带行为级黄金参考模型（Golden Reference Model）与自动化记分板（Scoreboard）的独立自测台，消灭时序竞态，跨越 L1 至 L2 的仿真鸿沟。
- **Trigger:** 自验证测试台生成、时钟毛刺假阳性、时序逻辑采样竞态、仿真失配。
- **Log signatures:** `tb_self_check`, `TB_FAILURE`, `TB_SUCCESS`, `Mismatches`, `Scoreboard`, `assertion failed`

## 核心架构原则

1. **时钟与时序竞态消除（Zero-Delta-Race Rule）**：
   - 激励施加在**时钟下降沿**（`@(negedge clk)`），避免与 DUT 的 `posedge clk` 产生仿真 Delta 竞态：
     ```systemverilog
     task drive(input bit rst_val, input [7:0] din);
         @(negedge clk);
         reset <= rst_val;
         data  <= din;
     endtask
     ```
   - 结果比对在**时钟上升沿之后延迟 1 个仿真时间单位**（`@(posedge clk); #1;`），确保非阻塞赋值全部结算完毕。
2. **黄金参考模型（Golden Behavioral Function / Task）**：
   - 在测试台内定义高层算法函数或影子寄存器作为基准，将 DUT 输出与黄金模型逐拍比对：
     ```systemverilog
     // 黄金参考与记分板
     if (dut_q !== expected_q) begin
         $display("TB_FAILURE: Mismatch at time %0t: DUT=%h, EXP=%h", $time, dut_q, expected_q);
         errors++;
     end
     ```
3. **四阶测试覆盖阶梯**：
   - 阶段 1：复位持续 3 个周期，验证复位状态输出为确定值（无 X/Z 态）。
   - 阶段 2：复位撤除，验证初始拍行为。
   - 阶段 3：边界向量与对抗样本（如同时拉高 reset 与 load，连续相同脉冲，重叠序列）。
   - 阶段 4：随机长序列（`repeat (50) ...`），全面覆盖状态转移路径。
4. **看门狗守护**：
   - 严禁死循环：必须加入 `#20000; $display("TB_FAILURE: Simulation watchdog timeout"); $finish;`。
