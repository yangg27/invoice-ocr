"""交付前检查：打包产物是否依赖「干净 Win10 之外的任何东西」。

做法：
1. 把包内所有 PE 文件（exe/dll/pyd）的导入表全部读出来；
2. 每个导入的 DLL 必须满足其一：包内已带、Win10 系统自带（白名单）、UCRT/API Set；
3. 单独核对 VC++ 运行库是否随包携带（原生 Win10 不自带 msvcp140 等）。

用法：python tools/check_offline_deps.py [dist目录]
"""
from __future__ import annotations

import os
import re
import sys

import pefile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Windows 10 自带（或系统组件）的 DLL，不需要随包携带
SYSTEM_DLLS = {
    "kernel32.dll", "user32.dll", "gdi32.dll", "advapi32.dll", "shell32.dll",
    "ole32.dll", "oleaut32.dll", "comdlg32.dll", "comctl32.dll", "ws2_32.dll",
    "crypt32.dll", "bcrypt.dll", "ncrypt.dll", "secur32.dll", "iphlpapi.dll",
    "dnsapi.dll", "winmm.dll", "version.dll", "imm32.dll", "shcore.dll",
    "dwmapi.dll", "uxtheme.dll", "msimg32.dll", "psapi.dll", "setupapi.dll",
    "cfgmgr32.dll", "userenv.dll", "netapi32.dll", "mpr.dll", "wintrust.dll",
    "wtsapi32.dll", "opengl32.dll", "glu32.dll", "d3d11.dll", "dxgi.dll",
    "d2d1.dll", "dcomp.dll", "dwrite.dll", "uiautomationcore.dll", "oleacc.dll",
    "propsys.dll", "rpcrt4.dll", "sechost.dll", "ntdll.dll", "msvcrt.dll",
    "ucrtbase.dll", "shlwapi.dll", "urlmon.dll", "wininet.dll", "winhttp.dll",
    "dbghelp.dll", "powrprof.dll", "authz.dll", "mswsock.dll", "dxva2.dll",
    "evr.dll", "mf.dll", "mfplat.dll", "mfreadwrite.dll", "avrt.dll",
    "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "concrt140.dll",
    "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll", "vcomp140.dll",
}

REQUIRED_VC = [
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
]

VC_PATTERN = re.compile(r"^(msvcp140|vcruntime140|concrt140|vcamp140|vccorlib140|vcomp140)")

WINDOWS_10_BASE = {
    "api-ms-win-crt-runtime-l1-1-0.dll",
    "api-ms-win-crt-heap-l1-1-0.dll",
    "api-ms-win-core-synch-l1-1-0.dll",
}


def is_system(name: str) -> bool:
    n = name.lower()
    if n in SYSTEM_DLLS:
        return True
    if n.startswith(("api-ms-win-", "ext-ms-", "api-ms-win-core-", "ucrtbase")):
        return True
    return False


def collect_bundled(root: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for base, _dirs, files in os.walk(root):
        for f in files:
            low = f.lower()
            if low.endswith((".dll", ".pyd", ".exe")):
                found.setdefault(low, os.path.join(base, f))
    return found


def imports_of(path: str) -> list[str]:
    names: list[str] = []
    try:
        pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(
            directories=[
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
            ]
        )
        for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
            for entry in getattr(pe, attr, []) or []:
                try:
                    names.append(entry.dll.decode("ascii", "ignore").lower())
                except Exception:  # noqa: BLE001
                    continue
        pe.close()
    except Exception as exc:  # noqa: BLE001
        print(f"  ! 读取失败 {os.path.basename(path)}: {exc}")
    return names


def main() -> int:
    dist = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "电子发票识别助手")
    dist = os.path.abspath(dist)
    if not os.path.isdir(dist):
        print(f"目录不存在: {dist}")
        return 2

    sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    bundled = collect_bundled(dist)
    print(f"包内文件数：{len(bundled)}（含 DLL/PYD/EXE）")
    print("包内 VC 运行库：")
    for dll in REQUIRED_VC:
        print(f"  {'✓' if dll in bundled else '✗'} {dll}")
    missing_vc = [d for d in REQUIRED_VC if d not in bundled]

    binaries = list(bundled.values())
    unresolved: dict[str, set[str]] = {}
    for path in binaries:
        for dep in imports_of(path):
            if dep in bundled or is_system(dep):
                continue
            unresolved.setdefault(dep, set()).add(os.path.basename(path))

    provided_by_windows: list[str] = []
    must_fix: dict[str, set[str]] = {}
    for dep, users in unresolved.items():
        if VC_PATTERN.match(dep):
            must_fix[dep] = users  # VC 运行库必须随包，干净 Win10 不一定有
        elif os.path.exists(os.path.join(sys32, dep)):
            provided_by_windows.append(dep)
        else:
            must_fix[dep] = users

    if provided_by_windows:
        print()
        print("由 Win10 系统提供（已确认 System32 中存在）：")
        for dep in sorted(provided_by_windows):
            print(f"  · {dep}")

    print()
    if must_fix:
        print("以下依赖必须补进包内：")
        for dep, users in sorted(must_fix.items()):
            print(f"  ✗ {dep}   ← 被 {', '.join(sorted(users)[:3])} 引用")
    else:
        print("导入表依赖闭合：所有外部 DLL 均随包携带，或由 Win10 系统提供。")

    print()
    if missing_vc:
        print(f"结论：缺少 VC 运行库 {missing_vc}，原生 Win10 可能无法启动。")
        return 1
    if must_fix:
        print(f"结论：有 {len(must_fix)} 个依赖未闭合，需要补进包内。")
        return 1
    print("结论：依赖闭合，可在未安装任何运行库的 Win10 上解压直接运行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())