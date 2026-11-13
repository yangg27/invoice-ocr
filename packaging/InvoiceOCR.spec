# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['../run.py'],
    pathex=['D:/工作文件/deepseek-harness/default-workspace/invoice-ocr/build/stage', 'D:/工作文件/deepseek-harness/default-workspace/invoice-ocr/build/stage/rapidocr_onnxruntime'],
    binaries=[],
    datas=[('D:/工作文件/deepseek-harness/default-workspace/invoice-ocr/assets/app.ico', 'assets'), ('D:/工作文件/deepseek-harness/default-workspace/invoice-ocr/build/stage/rapidocr_onnxruntime', 'rapidocr_onnxruntime')],
    hiddenimports=['ch_ppocr_v3_det', 'ch_ppocr_v3_rec', 'ch_ppocr_v2_cls'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'pandas', 'scipy', 'tkinter', 'PyQt5', 'IPython'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='InvoiceOCR',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['D:/工作文件/deepseek-harness/default-workspace/invoice-ocr/assets/app.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='InvoiceOCR',
)
