"""Deterministic interface contract validation and non-destructive header patching.

Zero-token, sub-millisecond AST & regex based guard to eliminate L0 contract violations.
"""

from __future__ import annotations

import re
from typing import List, Dict, Tuple


class DeterministicGuard:
    @staticmethod
    def parse_header_ports(code: str) -> Tuple[str, List[Dict[str, any]]]:
        """Extract module name and parsed ports without invoking LLM."""
        m = re.search(r"module\s+([A-Za-z_]\w*)\s*(?:#\s*\([^)]*\)\s*)?\((.*?)\)\s*;", code, re.DOTALL)
        if not m:
            return "", []
        mod_name = m.group(1)
        raw_ports = m.group(2)

        # Strip comments
        raw_ports = re.sub(r"//.*", "", raw_ports)
        raw_ports = re.sub(r"/\*.*?\*/", "", raw_ports, flags=re.DOTALL)

        parsed = []
        direction = None
        width = 1
        for raw in raw_ports.split(","):
            item = raw.strip()
            if not item:
                continue
            dm = re.search(r"\b(input|output|inout)\b", item, re.IGNORECASE)
            if dm:
                direction = dm.group(1).lower()
                item = item[dm.end():].strip()
                width = 1
            if direction is None:
                continue
            rm = re.search(r"\[\s*(-?\d+)\s*:\s*(-?\d+)\s*\]", item)
            if rm:
                width = abs(int(rm.group(1)) - int(rm.group(2))) + 1
                item = item[:rm.start()] + item[rm.end():]
            item = re.sub(r"\b(?:wire|reg|logic|signed|unsigned|var)\b", " ", item)
            nm = re.search(r"\b([A-Za-z_]\w*)\b", item)
            if nm:
                parsed.append({"name": nm.group(1), "direction": direction, "width": width})
        return mod_name, parsed

    @classmethod
    def enforce_contract(
        cls, source_code: str, expected_top: str, expected_ports: List[Dict[str, any]]
    ) -> Tuple[bool, str, str]:
        """Verify and perform surgical zero-token in-place header repair.

        Returns: (is_valid, report_message, patched_code)
        """
        got_name, got_ports = cls.parse_header_ports(source_code)
        if not got_name:
            return False, "SYNTAX_ERROR: No module declaration found.", source_code

        issues = []
        if got_name != expected_top:
            issues.append(f"Top module mismatch: expected '{expected_top}', got '{got_name}'")

        got_map = {p["name"]: p for p in got_ports}
        want_map = {p["name"]: p for p in expected_ports}

        # Check missing or extra
        missing = [name for name in want_map if name not in got_map]
        if missing:
            issues.append(f"Missing required ports: {missing}")

        for name, wp in want_map.items():
            if name in got_map:
                gp = got_map[name]
                if wp["direction"] != gp["direction"]:
                    issues.append(f"Port '{name}' direction mismatch: want {wp['direction']}, got {gp['direction']}")
                if wp["width"] != gp["width"]:
                    issues.append(f"Port '{name}' width mismatch: want {wp['width']}-bit, got {gp['width']}-bit")

        # Non-destructive header reconstruction if deviation detected
        if issues and expected_ports:
            port_decls = []
            for p in expected_ports:
                w_str = f"[{p['width']-1}:0] " if p["width"] > 1 else ""
                # Infer reg requirement if assigned with <= or = in always block
                is_out = (p["direction"] == "output")
                needs_reg = is_out and bool(re.search(rf"\b{p['name']}\s*(?:<=|\+=|-=|=)(?!=)", source_code))
                has_reg = "reg " if needs_reg else ""
                port_decls.append(f"    {p['direction']} {has_reg}{w_str}{p['name']}")

            new_header = f"module {expected_top} (\n" + ",\n".join(port_decls) + "\n);"
            patched = re.sub(
                r"module\s+[A-Za-z_]\w*\s*(?:#\s*\([^)]*\)\s*)?\((.*?)\)\s*;",
                new_header,
                source_code,
                count=1,
                flags=re.DOTALL,
            )
            # Re-check patched code
            check_top, check_ports = cls.parse_header_ports(patched)
            if check_top == expected_top and len(check_ports) == len(expected_ports):
                return True, "AUTO_PATCHED: Header reconciled with specification: " + "; ".join(issues), patched

        if issues:
            return False, "INTERFACE_VIOLATION: " + "; ".join(issues), source_code
        normalized = cls._normalize_output_types(source_code, expected_ports)
        if normalized != source_code:
            return True, "CONTRACT_SATISFIED: normalized output driver declarations", normalized
        return True, "CONTRACT_SATISFIED", source_code

    @staticmethod
    def _normalize_output_types(source: str, expected_ports: List[Dict[str, any]]) -> str:
        """Keep output net/reg type consistent with the generated driver."""
        header_match = re.search(
            r"module\s+[A-Za-z_]\w*\s*(?:#\s*\([^)]*\)\s*)?\((.*?)\)\s*;",
            source, re.DOTALL,
        )
        if not header_match:
            return source
        header = header_match.group(1)
        body = source[header_match.end():]
        patched_header = header
        for p in expected_ports:
            if p.get("direction") != "output":
                continue
            name = re.escape(str(p.get("name", "")))
            if not name:
                continue
            continuous = bool(re.search(rf"\bassign\s+{name}\s*=", body))
            procedural = (not continuous) and bool(re.search(rf"\b{name}\s*(?:<=|\+=|-=|(?<![<>=!])=(?!=))", body))
            if continuous and not procedural:
                patched_header = re.sub(
                    rf"(\boutput\s+)(?:reg|logic|wire|var)?\s*((?:\[[^]]+\]\s*)?{name}\b)",
                    r"\1\2", patched_header,
                )
            elif procedural:
                patched_header = re.sub(
                    rf"(\boutput\s+)(?!(?:reg)\b)(?:(?:logic|wire|var)\s+)?((?:\[[^]]+\]\s*)?{name}\b)",
                    r"\1reg \2", patched_header,
                )
        if patched_header == header:
            return source
        return source[:header_match.start(1)] + patched_header + source[header_match.end(1):]
