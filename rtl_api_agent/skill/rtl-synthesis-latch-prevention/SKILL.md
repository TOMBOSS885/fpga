---
name: rtl-synthesis-latch-prevention
description: Techniques to detect and eliminate unintended latch inference and non-synthesizable constructs in Vivado synthesis.
---

# Vivado synthesis repair without changing behavior

先保留题面的接口与行为，再修复 Vivado 报告的具体构造。可综合性检查不能覆盖功能规格，普通告警不自动等于失败。

- **Trigger:** Vivado 综合报告意外锁存器、多驱动冲突、DRC 错误、不可综合构造。
- **Log signatures:** `Synth 8-327`, `inferring latch for variable`, `DRC`, `multi-driven net`, `Synth 8-6156`, `unsupported construct`

- 对题面要求的组合逻辑，覆盖全部应定义的赋值路径，避免意外 latch；使用默认赋值或完整分支。若题面明确要求电平锁存器，保留使能关闭时的保持语义，不能用默认清零消除它。
- 不给同一寄存器增加互相竞争的过程驱动。合并时钟/复位逻辑时保留原有有效边沿、同步性和优先级。
- DUT 不使用测试台的延时、仿真终止、文件读取、动态仿真进程或打印来实现功能。固定上界循环和 generate 可用于描述硬件。
- `initial` 不一概等于不可综合：Vivado 支持的常量寄存器/存储器初始化可能是题面行为的一部分。保留指定的上电值；只修复确实不被目标工具支持的用法，不能删除初始化来换取编译通过。
- 以首个有效 ERROR 和相关上下文定位原因；检查完整综合成功标记。许可证、缺失库、工具崩溃和墙钟超时属于环境问题，应停止 RTL 修复。
