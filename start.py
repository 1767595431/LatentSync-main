"""启动网页 / API。监听地址和端口只读根目录 config.yaml。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
old_pp = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = str(ROOT) if not old_pp else str(ROOT) + os.pathsep + old_pp
_env_bin = str(Path(sys.executable).resolve().parent)
_path = os.environ.get("PATH", "")
if _env_bin not in _path.split(os.pathsep):
    os.environ["PATH"] = _env_bin + (os.pathsep + _path if _path else "")


def ensure_worker() -> None:
    from webapp.config import WORKER_LOG_PATH, ensure_dirs
    from webapp.gpu_runtime import worker_alive

    ensure_dirs()
    if worker_alive():
        return
    with open(WORKER_LOG_PATH, "ab") as log:
        subprocess.Popen(
            [sys.executable, "-m", "webapp.worker"],
            cwd=str(ROOT),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=os.environ.copy(),
        )


def main() -> None:
    import uvicorn

    from webapp.config import server_host, server_port

    host = server_host()
    port = server_port()
    ensure_worker()
    print(f"API  {host}:{port}", flush=True)
    uvicorn.run("webapp.app:app", host=host, port=port)


if __name__ == "__main__":
    main()
