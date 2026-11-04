"""Safe diagnostic for Windows UVC cameras used by DriverThermalQC.

Run with the application and HIKMICRO Analyzer CLOSED:
    python tools/diagnose_uvc.py

This intentionally uses only DirectShow + normal decoded frames. It does not
request raw formats, send vendor USB commands, or modify camera settings.
"""
from __future__ import annotations

import argparse
import sys
import time

import cv2
import numpy as np


def fourcc_text(value: float) -> str:
    code = int(value)
    return "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4)).strip()


def inspect(index: int, frames_to_read: int) -> bool:
    print(f"\n=== Camera index {index} (DirectShow) ===", flush=True)
    try:
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    except Exception as exc:
        print(f"OPEN_EXCEPTION: {type(exc).__name__}: {exc}")
        return False

    if not cap.isOpened():
        print("OPEN: failed")
        cap.release()
        return False

    print("OPEN: yes")
    print(f"backend: {cap.getBackendName()}")
    print(
        "properties: "
        f"width={cap.get(cv2.CAP_PROP_FRAME_WIDTH):.0f}, "
        f"height={cap.get(cv2.CAP_PROP_FRAME_HEIGHT):.0f}, "
        f"fps={cap.get(cv2.CAP_PROP_FPS):.2f}, "
        f"fourcc={fourcc_text(cap.get(cv2.CAP_PROP_FOURCC))!r}"
    )

    good = 0
    for n in range(frames_to_read):
        ok, frame = cap.read()
        if not ok or frame is None:
            print(f"frame {n + 1}: READ_FAILED")
            continue
        arr = np.asarray(frame)
        good += 1
        print(
            f"frame {n + 1}: shape={arr.shape}, dtype={arr.dtype}, "
            f"bytes={arr.nbytes}, min={int(arr.min())}, max={int(arr.max())}, "
            f"mean={float(arr.mean()):.3f}, std={float(arr.std()):.3f}, "
            f"nonzero={(float(np.count_nonzero(arr)) / arr.size):.1%}"
        )

    cap.release()
    print(f"SUMMARY: {good}/{frames_to_read} frames read")
    return good > 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-index", type=int, default=5)
    parser.add_argument("--frames", type=int, default=8)
    args = parser.parse_args()

    if args.max_index < 0 or args.frames < 1:
        parser.error("--max-index must be >= 0 and --frames must be >= 1")

    print(f"OpenCV: {cv2.__version__}")
    print("IMPORTANT: close DriverThermalQC and HIKMICRO Analyzer before running.")
    found = 0
    for index in range(args.max_index + 1):
        if inspect(index, args.frames):
            found += 1
        time.sleep(0.35)

    print(f"\nFinished. Devices yielding frames: {found}")
    print("Copy the complete console output back into the chat.")
    return 0 if found else 2


if __name__ == "__main__":
    sys.exit(main())
