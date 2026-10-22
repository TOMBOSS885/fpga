---
name: rtl-interface-contract
description: Diagnose and repair Verilog modules that fail Vivado elaboration because the module name, port names, directions, or widths do not match the declared interface.
---

# Agent Skill: RTL Module Interface Contract

## Skill Metadata
- **Name:** `rtl-interface-contract`
- **Description:** 严格保证顶层模块名、端口名、端口方向、位宽与题面要求 100% 精确一致，彻底消灭 L0 致命错误。
- **Trigger:** `xelab` 报模块找不到、端口找不到，或 `xvlog` 拒绝模块头声明。
- **Log signatures:** `VRFC 10-3180`, `VRFC 10-2063`, `VRFC 10-4982`, `cannot find port`, `not found while processing module instance`, `Static elaboration`, `interface mismatch`

## 指引与硬性规则

1. **模块名绝对一致**：模块名必须与题面完全一致（包括大小写）。默认顶层模块名为 `TopModule`。
2. **端口名、方向与位宽**：
   - 严禁重命名端口（例如将 `q` 写成 `out`，将 `rst` 写成 `reset`）。
   - 严禁额外增加未声明的端口。
   - 位宽声明必须严格遵循 `[W-1:0]` 降序习惯，严禁倒置为 `[0:W-1]`。
3. **输出类型说明**：
   - 可以在输出端口上添加 `reg` 或 `logic` 声明（如 `output reg [7:0] q`），这符合 Verilog 规范且不影响顶层例化。
4. **禁止内置测试台**：
   - 生成文件中不得包含 `initial forever #5 clk = ~clk;` 或 `$finish;`。
