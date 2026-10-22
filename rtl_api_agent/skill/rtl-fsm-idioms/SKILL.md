---
name: rtl-fsm-idioms
description: Idiomatic finite state machine patterns in Verilog/SystemVerilog, ensuring clean state transition, output registration, and zero latch inference.
---

# FSM transitions and output timing

状态编码、复位状态、转换表和输出时序以题面为准。不要仅为套用三段式模板增加输出寄存器或改变状态编码。

- **Trigger:** 序列检测错误、状态转移死锁、组合反馈环路、FSM 编译告警。
- **Log signatures:** `FSM`, `state machine`, `unreachable state`, `combinational loop`, `state transition`

- 将每个状态的条件、次态、输出列清楚。Moore 输出依赖当前状态；Mealy 输出还依赖当前输入。`state`、`next_state` 和寄存输出代表不同周期，不能互换。
- 简单寄存状态与组合次态分开实现有助检查；组合块先给次态和输出合理默认值，再覆盖各分支。默认恢复到复位态只有在题面允许时才采用，不改变明确规定的非法状态行为。
- 若接口直接暴露 one-hot 状态位、要求特定编码或只要求组合次态逻辑，必须照做，不能把整个问题替换成自选编码的完整 FSM。
- 为每条规定的转移构造短输入序列；检查自环、连续输入、重叠前缀、复位中断和输出生效周期。仅在题目要求时增加寄存输出。
