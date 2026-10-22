---
name: rtl-clock-reset-conventions
description: Rules and patterns for clocking and reset implementations in FPGA synthesis, preventing multi-driven nets, incorrect sensitivity lists, and simulation-synthesis mismatches.
---

# Clock and reset semantics

题面是唯一行为依据。不得从通用模板推断上升沿、同步复位、复位值或控制优先级；没有复位端口时不新增复位。

- **Trigger:** 时钟边沿、同步/异步复位、复位极性、使能与复位交互、逐拍偏差。
- **Log signatures:** `multi-driven net`, `sensitivity list`, `asynchronous reset`, `uninitialized register`, `clock domain`, `Mismatches`

- 先列出题面规定的有效时钟边沿、复位极性、同步性、复位值与使能条件。保留下降沿、双边沿或电平敏感行为，不能统一改成 `posedge clk`。
- 同步复位仅在有效时钟边沿判断；异步复位必须包含规定的复位有效沿。例如低有效异步复位使用 `or negedge rst_n` 与 `if (!rst_n)`。名称只是线索，不覆盖文字规格。
- 复位、load、enable 的嵌套顺序由题面决定：使能可能屏蔽同步复位，也可能由复位覆盖使能。逐项检查同时有效的情况。
- 边沿触发寄存器使用非阻塞赋值；同一拍右侧读取旧值。时钟使能无效时保持寄存器值，不要把组合路径默认赋值规则套到寄存器。
- 自测在非有效沿驱动输入、NBA 更新后比较；分别检查复位在时钟边沿之间有效、撤销后的第一拍、使能关闭与连续有效。
