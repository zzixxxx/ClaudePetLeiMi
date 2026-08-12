"""petcore.update — 自动更新: 对比远端 version.txt, 下载 zip 覆盖 BASE.

重启进程 / 通知由各壳自行处理.
"""
import os
import urllib.request

UPDATE_VER_URL = ("https://raw.githubusercontent.com/zzixxxx/"
                  "ClaudePetLeiMi/main/version.txt")
UPDATE_ZIP_URL = ("https://github.com/zzixxxx/ClaudePetLeiMi/"
                  "archive/refs/heads/main.zip")
UPDATE_CHECK_S = 24 * 3600


def local_version(base):
    try:
        with open(os.path.join(base, "version.txt"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "0"


def remote_version():
    req = urllib.request.Request(UPDATE_VER_URL,
                                 headers={"User-Agent": "ClaudePetLeiMi"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8", "ignore").strip()


def download_and_apply(base):
    """下载 main 分支 zip 并覆盖到 base 目录 (dirs_exist_ok)."""
    import shutil
    import tempfile
    import zipfile
    zpath = os.path.join(tempfile.gettempdir(), "ClaudePetLeiMi_upd.zip")
    req = urllib.request.Request(UPDATE_ZIP_URL,
                                 headers={"User-Agent": "ClaudePetLeiMi"})
    with urllib.request.urlopen(req, timeout=120) as r, \
            open(zpath, "wb") as f:
        shutil.copyfileobj(r, f)
    exdir = os.path.join(tempfile.gettempdir(), "ClaudePetLeiMi_upd")
    shutil.rmtree(exdir, ignore_errors=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(exdir)
    shutil.copytree(os.path.join(exdir, "ClaudePetLeiMi-main"), base,
                    dirs_exist_ok=True)
