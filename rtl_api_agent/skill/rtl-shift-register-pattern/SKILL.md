---
name: rtl-shift-register-pattern
description: Implement specified sequence overlap, output timing, LFSR taps, shift direction and seed without changing required state behavior.
---

# Sequence detection and LFSR semantics

移位寄存器和 FSM 都是可选实现；题面要求的状态编码、转换和可观察输出必须保留。不得自行选择重叠规则、种子或反馈多项式。

- **Trigger:** 序列检测、重叠子串、LFSR 周期、移位方向、抽头和反馈。
- **Log signatures:** `sequence`, `detector`, `lfsr`, `shift register`, `overlapping`, `feedback`, `taps`

- 逐位区分“接收当前输入前的历史”和“加入当前输入后的窗口”。非阻塞赋值右侧读取旧值；使用旧窗口或新窗口比较会相差一拍。输出是否注册、在哪一拍有效由题面决定。
- 有效窗口尚未填满时，复位填充值可能制造假的匹配，尤其是前导零模式。按题面确定复位后哪些历史有效，必要时跟踪已接收位数。
- 可重叠检测在匹配后保留仍为目标前缀的后缀；非重叠检测按规定清除历史。覆盖连续匹配、重叠匹配、复位打断及相近但不匹配的流。
- LFSR 按每一位的次态方程确认 Fibonacci/Galois 形式、左/右移、索引方向、抽头与 XOR/XNOR；不能把任意示例的抽头直接套入题目。
- XOR LFSR 的全零状态可能锁死，但若题面规定零种子或未要求自动恢复，不得自行改为非零或增加恢复逻辑。复位、load 与 enable 的优先级仍以题面为准。
