"""调试工具：打印 PDF/图片的坐标版式，用于校准解析规则。

用法：
    python tools/dump_layout.py "samples/xxx.pdf" [--lines] [--parse]
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core import parser  # noqa: E402
from app.core.textextract import extract_any  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    path = sys.argv[1]
    show_lines = "--lines" in sys.argv or len(sys.argv) == 2
    do_parse = "--parse" in sys.argv
    layouts = extract_any(path)
    for lay in layouts:
        print(f"=== page {lay.page_no} {lay.width:.1f}x{lay.height:.1f} source={lay.source} "
              f"words={len(lay.words)}")
        if show_lines:
            for ln in lay.line_list:
                print(f" y={ln.cy:7.2f} x={ln.x0:7.2f}..{ln.x1:7.2f} | {ln.text}")
                for w in ln.words:
                    print(f"      [{w.x0:7.2f},{w.y0:7.2f}]-[{w.x1:7.2f},{w.y1:7.2f}] size={w.size:4.1f} {w.text!r}")
        if do_parse:
            res = parser.parse_layout(lay, os.path.basename(path))
            print(f"--- template={res.template} conf={res.confidence} warnings={res.warnings}")
            for k, v in res.fields.items():
                print(f"    {k:18} = {v!r}")
            for it in res.items:
                print(f"    item: {it.to_dict()}")


if __name__ == "__main__":
    main()