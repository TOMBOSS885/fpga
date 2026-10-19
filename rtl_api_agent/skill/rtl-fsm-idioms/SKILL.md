---
name: rtl-fsm-idioms
description: Idiomatic finite state machine patterns in Verilog/SystemVerilog, ensuring clean state transition, output registration, and zero latch inference.
---

# Agent Skill: RTL Finite State Machine Idioms

## Skill Metadata
- **Name:** `rtl-fsm-idioms`
- **Description:** 规范有限状态机（FSM）编码，采用标准三段式状态机，消除毛刺与状态死锁。
- **Trigger:** 序列检测错误、状态转移死锁、组合反馈环路、FSM 编译告警。
- **Log signatures:** `FSM`, `state machine`, `unreachable state`, `combinational loop`, `state transition`

## 推荐标准三段式 FSM 架构

```verilog
// 1. 状态定义：独热码或二进制枚举
localparam S_IDLE  = 2'd0;
localparam S_READ  = 2'd1;
localparam S_DONE  = 2'd2;

reg [1:0] state, next_state;

// 第一段：时序逻辑转移当前状态 (同步/异步复位)
always @(posedge clk) begin
    if (reset) begin
        state <= S_IDLE;
    end else begin
        state <= next_state;
    end
end

// 第二段：组合逻辑计算次态 (必须带 default，所有信号均赋默认初值)
always @(*) begin
    next_state = state; // 防锁存器兜底
    case (state)
        S_IDLE: if (start) next_state = S_READ;
        S_READ: if (finish) next_state = S_DONE;
        S_DONE: next_state = S_IDLE;
        default: next_state = S_IDLE;
    endcase
end

// 第三段：输出逻辑 (建议时序打拍输出或清晰组合逻辑)
always @(posedge clk) begin
    if (reset) begin
        out_valid <= 1'b0;
    end else begin
        out_valid <= (next_state == S_DONE);
    end
end
```
