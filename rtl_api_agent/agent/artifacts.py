"""Conservative SV artifact extraction and interface checks, not an RTL compiler."""
from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass, field

IDENT = r"[A-Za-z_][A-Za-z0-9_$]*"
MODULE = re.compile(rf"\bmodule\s+(?:automatic\s+)?({IDENT})\b.*?\bendmodule\b", re.S)


def uncomments(text: str) -> str:
    return re.sub(r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*',
                  lambda m: m.group() if m.group().startswith('"') else " " * len(m.group()), text, flags=re.S)


def expression(expr: str, parameters: dict[str, int]) -> int | None:
    if len(expr) > 256:
        return None
    expr = re.sub(r"(\d+)'[sS]?([bBdDhHoO])([0-9a-fA-F_]+)",
                  lambda m: str(int(m[3].replace("_", ""), {"b": 2, "o": 8, "d": 10, "h": 16}[m[2].lower()])), expr)
    expr = expr.replace("$clog2", "clog2")
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.FloorDiv: operator.floordiv, ast.Div: operator.floordiv,
           ast.Mod: operator.mod, ast.LShift: operator.lshift, ast.RShift: operator.rshift}

    def evaluate(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, int):
            return n.value
        if isinstance(n, ast.Name):
            return parameters[n.id]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            return evaluate(n.operand) * (-1 if isinstance(n.op, ast.USub) else 1)
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            a, b = evaluate(n.left), evaluate(n.right)
            if abs(a) > 2**30 or abs(b) > 100000 or isinstance(n.op, (ast.LShift, ast.RShift)) and not 0 <= b <= 30:
                raise ValueError("oversized expression")
            return ops[type(n.op)](a, b)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "clog2" and len(n.args) == 1:
            value = evaluate(n.args[0])
            return max(0, (value - 1).bit_length())
        raise ValueError("unsupported expression")
    try:
        return evaluate(ast.parse(expr.strip(), mode="eval").body)
    except (ValueError, SyntaxError, KeyError, TypeError, ZeroDivisionError, OverflowError):
        return None


@dataclass(frozen=True)
class Port:
    name: str
    direction: str
    width: int | None = 1
    signed: bool | None = None


@dataclass
class Contract:
    top: str = "TopModule"
    ports: list[Port] = field(default_factory=list)
    source: str = "unknown"


def parse_ports(text: str, parameters: dict[str, int]) -> list[Port]:
    ports, direction, width, signed = [], None, 1, False
    for item in re.split(r",|;", text):
        dm = re.search(r"\b(input|output|inout)\b", item)
        if dm:
            direction, item, width, signed = dm[1], item[dm.end():], 1, False
        if not direction:
            continue
        if re.search(r"\bsigned\b", item):
            signed = True
        intrinsic = re.search(r"\b(byte|shortint|int|integer|longint|time)\b", item)
        if intrinsic:
            width = {"byte": 8, "shortint": 16, "int": 32, "integer": 32, "longint": 64, "time": 64}[intrinsic[1]]
            signed = intrinsic[1] != "time" and not bool(re.search(r"\bunsigned\b", item))
        ranges = re.findall(r"\[([^\]]+)\]", item)
        if ranges:
            width = 1
            for r in ranges:
                bounds = [expression(x, parameters) for x in r.split(":")]
                if len(bounds) != 2 or any(x is None for x in bounds):
                    width = None
                    break
                width *= abs(bounds[0] - bounds[1]) + 1
        clean = re.sub(r"\[[^\]]*\]", " ", item.split("=", 1)[0])
        clean = re.sub(r"\b(wire|reg|logic|bit|signed|unsigned|var|byte|shortint|int|integer|longint|time)\b", " ", clean)
        name = re.search(IDENT, clean)
        if name:
            ports.append(Port(name[0], direction, width, signed))
    return ports


def module_contract(code: str, top: str | None = None) -> Contract:
    code = uncomments(code)
    mods = list(MODULE.finditer(code))
    mod = next((m for m in mods if m[1] == top), None) if top else next(iter(mods), None)
    if mod is None:
        return Contract(top or "TopModule")
    body = mod[0]
    parameters = {}
    for p in re.finditer(rf"(?:\bparameter\s+(?:(?:integer|int|unsigned|signed)\s+)*)?\b({IDENT})\s*=\s*([^,;)]+)", body):
        value = expression(p[2], parameters)
        if value is not None:
            parameters[p[1]] = value
    start = re.match(rf"module\s+(?:automatic\s+)?{re.escape(mod[1])}\b", body).end()
    pos = start
    while pos < len(body) and body[pos].isspace():
        pos += 1
    if pos < len(body) and body[pos] == "#":
        pos += 1
        while pos < len(body) and body[pos].isspace():
            pos += 1
        depth = 0
        while pos < len(body):
            char = body[pos]; pos += 1
            depth += (char == "(") - (char == ")")
            if depth == 0:
                break
    header_end = body.find(";", pos)
    header = body[pos:header_end]
    if re.search(r"\b(input|output|inout)\b", header):
        ports = parse_ports(header.strip().removeprefix("(").removesuffix(")"), parameters)
    else:
        ports = []
        for decl in re.findall(r"\b(?:input|output|inout)\b[^;]+;", body[header_end + 1:]):
            ports.extend(parse_ports(decl, parameters))
    return Contract(mod[1], ports, "module_header")


def task_contract(prompt: str, interface: str = "") -> Contract:
    for text in [interface, prompt]:
        if re.search(r"\bmodule\s+" + IDENT + r"\s*(?:#\s*\(|\()", uncomments(text)):
            parsed = module_contract(text + ("\nendmodule" if "endmodule" not in text else ""))
            if parsed.ports:
                return parsed
    match = re.search(rf"\bmodule\s+(?:named\s+|called\s+)?({IDENT})", prompt, re.I)
    top = match[1] if match and match[1].lower() not in {"with", "that", "for", "the", "a"} else "TopModule"
    ports = []
    for line in (interface or prompt).splitlines():
        m = re.match(r"\s*[-*]?\s*(input|output|inout)\s+(.+)", line, re.I)
        if not m:
            continue
        direction, rest = m[1].lower(), m[2]
        bits = re.search(r"\(\s*(\d+)\s*(?:bits?|位)\s*\)", rest, re.I)
        rest = re.sub(r"\([^)]*\)", " ", rest).split("//", 1)[0]
        # The official bullet format has one port per line; also accept a,b.
        rest = re.split(r"\s+(?:is|with|:|-)\s*", rest)[0]
        parsed = parse_ports(direction + " " + rest, {})
        signed = True if re.search(r"\bsigned\b", rest) else None
        ports.extend(Port(p.name, p.direction, int(bits[1]) if bits else p.width, signed) for p in parsed)
    return Contract(top, ports, "prompt_ports" if ports else "top_only")


def extract_sv(reply: str, top: str, testbench: bool = False) -> str:
    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.S)
    if "<think>" in reply:
        return ""  # An unfinished reasoning block is not a code artifact.
    fences = re.findall(r"```[^\n]*\n(.*?)```", reply, re.S)
    blocks = [b for b in fences if MODULE.search(uncomments(b))] or [reply]
    chosen, names = [], set()
    for block in blocks:
        modules = list(MODULE.finditer(uncomments(block)))
        # Do not concatenate several alternative implementations of the top.
        if any(m[1] == top for m in modules) and top in names:
            continue
        retained, cursor = [], 0
        for mod in modules:
            name = mod[1]
            if name in names or (testbench and name != top) or (not testbench and name != top and re.match(r"(?:tb_|testbench)", name, re.I)):
                retained.append(block[cursor:mod.start()])
                cursor = mod.end()
                continue
            names.add(name)
            retained.append(block[cursor:mod.end()])
            cursor = mod.end()
        if retained:
            # Fenced source retains macros, packages and compilation-unit
            # typedefs before/between modules. Drop trailing prose/partial RTL.
            source = "".join(retained)
            if not fences:
                first = re.search(r"(?m)^\s*(?:`\w+|module\s|package\s|typedef\s|import\s|function\s|localparam\s)", source)
                source = source[first.start():] if first else ""
            chosen.append(source)
    if top not in names:
        return ""
    return "\n".join(chosen).strip() + "\n"


def preflight(code: str, contract: Contract) -> tuple[bool, str]:
    plain = uncomments(code)
    parsed = module_contract(code, contract.top)
    if not re.search(rf"\bmodule\s+(?:automatic\s+)?{re.escape(contract.top)}\b", plain):
        return False, f"Missing exact top module {contract.top}"
    if re.search(r"\$(?:finish|stop|fatal|display|write|system|fopen|readmem[bh])\b|\b(?:force|release|fork|join|real|shortreal)\b|`include\b|#\s*\d", plain):
        return False, "DUT contains simulation-only operations, external files, or delays"
    expected, actual = {p.name: p for p in contract.ports}, {p.name: p for p in parsed.ports}
    if expected:
        if expected.keys() != actual.keys():
            return False, f"Port mismatch: expected {sorted(expected)}; received {sorted(actual)}"
        for name, p in expected.items():
            q = actual[name]
            if p.direction != q.direction or (p.width is not None and q.width is not None and p.width != q.width) or (p.signed is not None and p.signed != q.signed):
                return False, f"Port {name}: expected {p}, received {q}"
    return True, "Interface checked; unknown expressions remain subject to xvlog/xelab"


def tb_shape(code: str, top: str) -> bool:
    plain = uncomments(code)
    if re.search(r"\$(?:system|fopen|fread|readmem[bh]|fscanf)\b|`include\b|\bDPI(?:-C)?\b", plain):
        return False
    # No assumptions about instance name, local output aliases, slices or which
    # operand is on the left. A shape check is not proof of oracle correctness.
    return (bool(re.search(r"\bmodule\s+tb_self_check\b", plain))
            and bool(re.search(rf"\b{re.escape(top)}\s*(?:#\s*\([^;]*?\)\s*)?{IDENT}\s*\(", plain, re.S))
            and "TB_RESULT" in plain and "$finish" in plain
            and bool(re.search(r"!==|===|!=|==", plain)))
