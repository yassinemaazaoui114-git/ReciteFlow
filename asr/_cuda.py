"""Make the pip-installed NVIDIA DLLs (nvidia-cublas-cu12, nvidia-cudnn-cu12) loadable on Windows.

The wheels put their DLLs in site-packages/nvidia/<lib>/bin, which Windows does not search,
so ctranslate2 fails with "Library cublas64_12.dll is not found or cannot be loaded".
"""

import os
import site
import sys

_done = False


def add_nvidia_dll_dirs():
    global _done
    if _done or sys.platform != "win32":
        return
    _done = True
    bases = list(site.getsitepackages())
    user = site.getusersitepackages()
    if isinstance(user, str):
        bases.append(user)
    for base in bases:
        nvidia = os.path.join(base, "nvidia")
        if not os.path.isdir(nvidia):
            continue
        for name in os.listdir(nvidia):
            bin_dir = os.path.join(nvidia, name, "bin")
            if os.path.isdir(bin_dir):
                os.add_dll_directory(bin_dir)
                # ctranslate2 loads cuBLAS/cuDNN lazily via LoadLibrary, which also honours PATH.
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
