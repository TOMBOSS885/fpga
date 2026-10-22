---
name: rtl-self-testbench-generator
description: Guidelines and patterns for synthesizing companion self-checking testbenches to verify RTL designs with Vivado xsim before official scoring.
---

# Agent Skill: RTL Self-Testbench Generator

## Skill Metadata
- **Name:** `rtl-self-testbench-generator`
- **Description:** 自动化合成轻量级自测试台（Self-Checking Testbench），调用 xsim 逐拍排查时序与功能逻辑错误，跨越 L2 仿真鸿沟。
- **Trigger:** 模块语法通过（L1）后，自主进行功能正确性验证。
- **Log signatures:** `ASSERTION FAILED`, `self_test`, `testbench failure`, `simulation mismatch`, `xsim`

## 测试台合成模式

```systemverilog
`timescale 1ns/1ps

module tb_self_check();
  reg clk;
  reg reset;
  // 根据端口定义输入寄存器与输出引线
  // ...
  
  // 实例化 DUT
  TopModule dut (.*);

  // 1. 生成 5ns 标准周期时钟 (200MHz)
  initial begin
    clk = 0;
    forever #2.5 clk = ~clk;
  end

  // 2. 仿真主流程
  initial begin
    int errors = 0;
    
    // 复位阶段
    reset = 1'b1;
    #15;
    reset = 1'b0;
    #10;
    
    // 注入题意测试激励与断言检测
    // if (dut.q !== expected) begin
    //   $display("TB_ERROR: at %t, expected %h, got %h", $time, expected, dut.q);
    //   errors++;
    // end
    
    #100;
    if (errors == 0) begin
      $display("TB_SUCCESS: All self-tests passed with 0 errors.");
    end else begin
      $display("TB_FAILURE: Total %0d mismatches detected.", errors);
    end
    $finish;
  end

endmodule
```
