"""测试样本定位：仓库不包含任何真实发票，测试时从本地目录动态读取。

查找顺序：
1. 环境变量 INVOICE_OCR_SAMPLES 指定的目录
2. <项目根>/samples 目录（递归查找 PDF）

找不到样本时，测试脚本给出明确提示并跳过，避免在公开仓库里硬编码真实票据名。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sample_dirs() -> list[str]:
    dirs = []
    env = os.environ.get("INVOICE_OCR_SAMPLES")
    if env:
        dirs.append(env)
    dirs.append(os.path.join(ROOT, "samples"))
    return dirs


def sample_pdfs(limit: int | None = None) -> list[str]:
    found: list[str] = []
    for base in sample_dirs():
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for f in sorted(files):
                if f.lower().endswith(".pdf"):
                    found.append(os.path.join(root, f))
            if limit and len(found) >= limit:
                break
        if found:
            break
    found.sort()
    return found[:limit] if limit else found


def require_pdfs(n: int = 1) -> list[str]:
    pdfs = sample_pdfs(n)
    if len(pdfs) < n:
        print(
            f"跳过：需要至少 {n} 份 PDF 样本，未找到。\n"
            "请把电子发票放进 samples/ 目录，或设置环境变量 INVOICE_OCR_SAMPLES 指向样本目录。"
        )
        sys.exit(0)
    return pdfs