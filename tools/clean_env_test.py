"""模拟「干净 Win10」环境验证：不带任何 Python/VC 环境变量、PATH 只剩系统目录。

用法：python tools/clean_env_test.py
前提：先执行 tools/make_delivery 的目录拷贝（或直接对 dist\\InvoiceOCR 跑）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools._samples import require_pdfs  # noqa: E402

DIST = os.path.join(ROOT, "dist")
PDF = require_pdfs(1)[0]  # 本地样本，仓库不含真实票据
IMG = os.environ.get("INVOICE_TEST_IMAGE") or os.path.join(ROOT, "logs", "clean_env_sample.png")

FAILS: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok:
        FAILS.append(name)


def clean_env(tmp: str) -> dict[str, str]:
    """只保留 Windows 自带变量，清掉一切 Python/开发环境痕迹。"""
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    return {
        "PATH": os.path.join(sysroot, "System32") + ";" + sysroot,
        "SYSTEMROOT": sysroot,
        "WINDIR": sysroot,
        "TEMP": tmp,
        "TMP": tmp,
        "USERPROFILE": os.environ.get("USERPROFILE", tmp),
        "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", tmp),
        "APPDATA": os.environ.get("APPDATA", tmp),
        "COMSPEC": os.path.join(sysroot, "System32", "cmd.exe"),
        "NUMBER_OF_PROCESSORS": os.environ.get("NUMBER_OF_PROCESSORS", "4"),
        "PROCESSOR_ARCHITECTURE": "AMD64",
        "OS": "Windows_NT",
    }


def run(exe: str, args: list[str], env: dict[str, str], timeout: int = 180) -> int:
    return subprocess.run([exe] + args, env=env, timeout=timeout, close_fds=True).returncode


def main() -> int:
    name = "电子发票识别助手"
    app_dir = os.path.join(DIST, name)
    if not os.path.isdir(app_dir):
        app_dir = os.path.join(DIST, "InvoiceOCR")
    exe = os.path.join(app_dir, "InvoiceOCR.exe")
    if not os.path.exists(exe):
        print(f"未找到 exe: {exe}")
        return 2

    # 输出写到「程序目录」（该目录由打包过程创建，权限正常）。
    # 不再用 tempfile.mkdtemp：本机安全策略会拒绝子进程写入父进程新建的目录。
    tmp = app_dir
    env = clean_env(tmp)

    # 期望值由本机直接解析同一份样本得到，避免在仓库里硬编码任何票据信息
    from app.core.engine import recognize_file
    from app.core.expense import ExpenseClassifier

    expected = recognize_file(PDF, ExpenseClassifier())[0].record

    # 截图样本：没有就用同一份 PDF 现场渲染一张
    if not os.path.exists(IMG):
        os.makedirs(os.path.dirname(IMG), exist_ok=True)
        import fitz

        with fitz.open(PDF) as doc:
            doc[0].get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), alpha=False).save(IMG)

    # ---- 1. 纯净环境下的 PDF 识别 ----
    out_pdf = os.path.join(tmp, "_selftest_pdf.json")
    if os.path.exists(out_pdf):
        os.remove(out_pdf)
    code = run(exe, ["--selftest", PDF, out_pdf], env)
    check("纯净环境 PDF 识别退出码", code == 0, str(code))
    if os.path.exists(out_pdf):
        d = json.load(open(out_pdf, encoding="utf-8"))
        r = d["records"][0] if d.get("ok") else {}
        check(
            "纯净环境 PDF 识别结果",
            bool(d.get("ok"))
            and r.get("invoice_number") == expected["invoice_number"]
            and abs((r.get("total_with_tax") or 0) - (expected["total_with_tax"] or 0)) < 0.01,
            f'{r.get("invoice_number")} / {r.get("total_with_tax")}',
        )
        os.remove(out_pdf)
    else:
        check("纯净环境 PDF 识别结果", False, "没有输出文件")

    # ---- 2. 纯净环境下的截图 OCR ----
    out_ocr = os.path.join(tmp, "_selftest_ocr.json")
    if os.path.exists(out_ocr):
        os.remove(out_ocr)
    code = run(exe, ["--selftest", IMG, out_ocr], env)
    check("纯净环境 OCR 识别退出码", code == 0, str(code))
    if os.path.exists(out_ocr):
        d = json.load(open(out_ocr, encoding="utf-8"))
        r = d["records"][0] if d.get("ok") else {}
        check(
            "纯净环境 OCR 识别结果",
            bool(d.get("ok")) and r.get("invoice_number") == expected["invoice_number"],
            f'{r.get("invoice_number")} / {r.get("total_with_tax")}',
        )
        os.remove(out_ocr)
    else:
        check("纯净环境 OCR 识别结果", False, "没有输出文件")

    # ---- 3. 只读目录：数据自动改存 LOCALAPPDATA ----
    ro_root = os.path.join(os.environ.get("LOCALAPPDATA", tmp), "invoice_ro_" + uuid.uuid4().hex[:6])
    os.makedirs(ro_root, exist_ok=True)
    ro = os.path.join(ro_root, "app")
    shutil.copytree(app_dir, ro)
    open(os.path.join(ro, "data"), "w").close()  # 用同名文件占住 data 目录，模拟不可写

    local = os.path.join(ro_root, "localappdata")
    os.makedirs(local, exist_ok=True)
    env2 = dict(env)
    env2["LOCALAPPDATA"] = local
    shot = os.path.join(ro_root, "ro.png")
    code = run(os.path.join(ro, "InvoiceOCR.exe"), ["--screenshot", shot], env2)
    check("只读目录下仍能启动", code == 0, str(code))
    fallback_db = os.path.join(local, "InvoiceOCR", "data", "invoices.db")
    check("只读目录下数据改存 LOCALAPPDATA", os.path.exists(fallback_db), fallback_db)
    check("只读目录下截图成功", os.path.exists(shot) and os.path.getsize(shot) > 10000)

    shutil.rmtree(ro_root, ignore_errors=True)
    print()
    if FAILS:
        print("失败项：", FAILS)
        return 1
    print("干净环境验证全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())