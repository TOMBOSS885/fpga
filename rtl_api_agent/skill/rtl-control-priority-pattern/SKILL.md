---
name: rtl-control-priority-pattern
description: Implement specification-defined reset, clear, load and enable priority, and verify simultaneous assertions in sequential RTL.
---

# Control priority from the specification

不得固定套用 reset/clear/load/enable 的排序。先按题面为每个目标寄存器确定优先级、复位值与保持条件。

- **Trigger:** 优先级竞争、load/enable/reset 同时有效、LFSR 种子加载冲突、多分支条件重叠。
- **Log signatures:** `priority`, `load`, `enable`, `clear`, `reset priority`, `control hazard`, `Mismatches`

- 对同一寄存器的互斥优先操作，优先使用明确的 `if ... else if ... else`。排列顺序必须对应题面；同步复位可能位于使能条件内部。
- 多个并列 `if` 若赋同一寄存器，后面的非阻塞赋值可能覆盖前面的值。检查是否为预期语义；更新不同寄存器的独立条件不必合并成优先链。
- 寄存器不赋新值表示保持；组合逻辑则需要覆盖所有要求定义的路径。不要混淆寄存器保持和意外锁存器。
- 自测覆盖控制信号两两同时有效、全部有效、全部无效，以及连续加载/使能；参考结果严格来自规定的优先级。不得假定隐藏测试的内容。
