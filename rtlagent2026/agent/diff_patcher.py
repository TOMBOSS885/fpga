"""Surgical Search-Replace patch applier with whitespace-tolerant fuzzy fallback.

Inspired by Aider and OpenCode: replaces targeted blocks without rewriting entire files.
"""

from __future__ import annotations

import re
from typing import Tuple


class DiffPatcher:
    SEARCH_REPLACE_PATTERN = re.compile(
        r"<<<<<<<\s*SEARCH[^\n]*\n(.*?)\n=======\s*\n(.*?)\n>>>>>>>\s*REPLACE",
        re.DOTALL,
    )

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        return "\n".join(line.rstrip() for line in text.strip().splitlines())

    @classmethod
    def apply_patch(cls, original_code: str, patch_text: str) -> Tuple[bool, str, str]:
        """Apply SEARCH/REPLACE diff blocks to original_code.

        Returns: (success, result_code_or_original, log_message)
        """
        original_code = original_code.replace("\r\n", "\n")
        had_final_newline = original_code.endswith("\n")
        patch_text = patch_text.replace("\r\n", "\n")
        matches = list(cls.SEARCH_REPLACE_PATTERN.finditer(patch_text))
        if not matches:
            return False, original_code, "No valid SEARCH/REPLACE blocks found in patch."

        working_code = original_code
        applied_count = 0

        for idx, m in enumerate(matches):
            search_block = m.group(1)
            replace_block = m.group(2)

            # 1. Exact match attempt
            if search_block in working_code:
                working_code = working_code.replace(search_block, replace_block, 1)
                applied_count += 1
                continue

            # 2. Fuzzy match attempt (whitespace-tolerant line sliding window)
            lines = working_code.splitlines()
            search_lines = [l.strip() for l in search_block.strip().splitlines() if l.strip()]
            n_s = len(search_lines)

            if n_s == 0:
                continue

            matched_idx = -1
            matched_len = 0

            # Scan with sliding window
            for i in range(len(lines)):
                w_lines = []
                w_indices = []
                for k in range(i, len(lines)):
                    if lines[k].strip():
                        w_lines.append(lines[k].strip())
                        w_indices.append(k)
                        if len(w_lines) == n_s:
                            break
                if w_lines == search_lines:
                    matched_idx = w_indices[0]
                    matched_len = w_indices[-1] - w_indices[0] + 1
                    break

            if matched_idx != -1:
                leading_indent = re.match(r"^\s*", lines[matched_idx]).group(0)
                raw_rep_lines = replace_block.splitlines()
                first_rep = next((line for line in raw_rep_lines if line.strip()), "")
                rep_base = re.match(r"^\s*", first_rep).group(0)
                rep_lines = []
                for line in raw_rep_lines:
                    if not line.strip():
                        rep_lines.append("")
                        continue
                    # Keep indentation relative to the replacement block's
                    # first line, then anchor the block at the matched line.
                    line_indent = re.match(r"^\s*", line).group(0)
                    relative = line_indent[len(rep_base):] if line_indent.startswith(rep_base) else line_indent
                    rep_lines.append(leading_indent + relative + line[len(line_indent):])
                new_lines = lines[:matched_idx] + rep_lines + lines[matched_idx + matched_len :]
                working_code = "\n".join(new_lines)
                applied_count += 1
            else:
                return False, original_code, f"Failed to locate SEARCH block #{idx+1} in source code."

        if had_final_newline and not working_code.endswith("\n"):
            working_code += "\n"
        return True, working_code, f"Successfully applied {applied_count} surgical patch blocks."
