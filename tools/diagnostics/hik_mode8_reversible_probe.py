"""Reversible Windows HIKMICRO stream-mode 8 probe.

Starts a DirectShow UVC stream first, then requests mode 8 through the
HIK extension unit. It records frame sizes and restores the exact mode
reported before the change, while the stream is still open.

Requires Windows, Python with opencv-python and comtypes.
Run with HIKMICRO Analyzer and DriverThermalQC closed.
"""
from __future__ import annotations

import ctypes
import json
import sys
import time
from ctypes import POINTER, byref, c_void_p, c_ulong, wintypes

if sys.platform != "win32":
    raise SystemExit("Windows only")

try:
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
    from comtypes.persist import IPropertyBag
    import cv2
except ImportError as exc:
    raise SystemExit("Install dependencies: python -m pip install comtypes opencv-python") from exc

CLSID_SYSTEM_DEVICE_ENUM = GUID("{62BE5D10-60EB-11D0-BD3B-00A0C911CE86}")
CLSID_VIDEO_INPUT_CATEGORY = GUID("{860BB310-5D01-11D0-BD3B-00A0C911CE86}")
IID_CREATE_DEV_ENUM = GUID("{29840822-5B84-11D0-BD3B-00A0C911CE86}")
IID_MONIKER = GUID("{0000000F-0000-0000-C000-000000000046}")
IID_ENUM_MONIKER = GUID("{00000102-0000-0000-C000-000000000046}")
IID_KS_CONTROL = GUID("{28F54685-06FD-11D2-B27A-00A0C9223196}")
IID_KS_TOPOLOGY_INFO = GUID("{720D4AC0-7533-11D0-A5D6-28DB04C10000}")
IID_BASE_FILTER = GUID("{56A86895-0AD4-11CE-B03A-0020AF0BA770}")
KSNODETYPE_DEV_SPECIFIC = GUID("{941C7AC0-C559-11D0-8A2B-00A0C9255AC1}")
HIK_XU_GUID = GUID("{A29E7641-DE04-47E3-8B2B-F4341AFF003B}")
KSPROPERTY_TYPE_GET = 1
KSPROPERTY_TYPE_SET = 2
KSPROPERTY_TYPE_TOPOLOGY = 0x10000000

class IMoniker(IUnknown):
    _iid_ = IID_MONIKER
    _methods_ = [
        COMMETHOD([], HRESULT, "GetClassID", (["out"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "IsDirty"),
        COMMETHOD([], HRESULT, "Load", (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "Save", (["in"], c_void_p), (["in"], wintypes.BOOL)),
        COMMETHOD([], HRESULT, "GetSizeMax", (["out"], POINTER(ctypes.c_ulonglong))),
        COMMETHOD([], HRESULT, "BindToObject", (["in"], c_void_p), (["in"], c_void_p),
                  (["in"], POINTER(GUID)), (["out"], POINTER(POINTER(IUnknown)))),
        COMMETHOD([], HRESULT, "BindToStorage", (["in"], c_void_p), (["in"], c_void_p),
                  (["in"], POINTER(GUID)), (["out"], POINTER(POINTER(IUnknown)))),
    ]

class IEnumMoniker(IUnknown):
    _iid_ = IID_ENUM_MONIKER
    _methods_ = [COMMETHOD([], HRESULT, "Next", (["in"], c_ulong),
                           (["out"], POINTER(POINTER(IMoniker))),
                           (["out"], POINTER(c_ulong)))]

class ICreateDevEnum(IUnknown):
    _iid_ = IID_CREATE_DEV_ENUM
    _methods_ = [COMMETHOD([], HRESULT, "CreateClassEnumerator", (["in"], POINTER(GUID)),
                           (["out"], POINTER(POINTER(IEnumMoniker))),
                           (["in"], wintypes.DWORD))]

class IKsControl(IUnknown):
    _iid_ = IID_KS_CONTROL
    _methods_ = [COMMETHOD([], HRESULT, "KsProperty", (["in"], c_void_p),
                           (["in"], c_ulong), (["in"], c_void_p),
                           (["in"], c_ulong), (["out"], POINTER(c_ulong)))]

class IKsTopologyInfo(IUnknown):
    _iid_ = IID_KS_TOPOLOGY_INFO
    _methods_ = [
        COMMETHOD([], HRESULT, "get_NumCategories", (["out"], POINTER(wintypes.DWORD))),
        COMMETHOD([], HRESULT, "get_Category", (["in"], wintypes.DWORD), (["out"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "get_NumConnections", (["out"], POINTER(wintypes.DWORD))),
        COMMETHOD([], HRESULT, "get_ConnectionInfo", (["in"], wintypes.DWORD), (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "get_NodeName", (["in"], wintypes.DWORD), (["out"], c_void_p),
                  (["in"], wintypes.DWORD), (["out"], POINTER(wintypes.DWORD))),
        COMMETHOD([], HRESULT, "get_NumNodes", (["out"], POINTER(wintypes.DWORD))),
        COMMETHOD([], HRESULT, "get_NodeType", (["in"], wintypes.DWORD), (["out"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "CreateNodeInstance", (["in"], wintypes.DWORD),
                  (["in"], POINTER(GUID)), (["out"], POINTER(POINTER(IUnknown)))),
    ]

class KSP_NODE(ctypes.Structure):
    _fields_ = [("Set", GUID), ("Id", c_ulong), ("Flags", c_ulong),
                ("NodeId", c_ulong), ("Reserved", c_ulong)]

def raw_ks_property(ks: IKsControl, prop: KSP_NODE, data_ptr: int | None, data_len: int):
    this_ptr = ctypes.cast(ks, c_void_p).value
    if not this_ptr:
        raise RuntimeError("Null IKsControl pointer")
    vtable = ctypes.cast(this_ptr, POINTER(POINTER(c_void_p))).contents
    call = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_void_p, c_ulong,
                              c_void_p, c_ulong, POINTER(c_ulong))(vtable[3])
    returned = c_ulong(0)
    hr = int(call(this_ptr, ctypes.byref(prop), ctypes.sizeof(prop),
                  data_ptr, data_len, byref(returned)))
    return hr, int(returned.value)

def get_property(ks: IKsControl, node_id: int, selector: int) -> bytes:
    prop = KSP_NODE(HIK_XU_GUID, selector,
                    KSPROPERTY_TYPE_GET | KSPROPERTY_TYPE_TOPOLOGY, node_id, 0)
    size_hr, required = raw_ks_property(ks, prop, None, 0)
    if not 1 <= required <= 512:
        raise RuntimeError(f"Selector {selector} size query failed: HRESULT "
                           f"0x{size_hr & 0xffffffff:08X}, bytes={required}")
    buf = ctypes.create_string_buffer(required)
    hr, returned = raw_ks_property(ks, prop, ctypes.cast(buf, c_void_p).value, required)
    if hr < 0:
        raise OSError(f"Selector {selector} GET failed: HRESULT 0x{hr & 0xffffffff:08X}")
    if returned > required:
        raise RuntimeError("KS returned more bytes than the allocated buffer")
    return buf.raw[:returned]

def set_property(ks: IKsControl, node_id: int, selector: int, payload: bytes):
    prop = KSP_NODE(HIK_XU_GUID, selector,
                    KSPROPERTY_TYPE_SET | KSPROPERTY_TYPE_TOPOLOGY, node_id, 0)
    buf = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
    hr, returned = raw_ks_property(ks, prop, ctypes.cast(buf, c_void_p).value, len(payload))
    if hr < 0:
        raise OSError(f"Selector {selector} SET {payload.hex(' ')} failed: "
                      f"HRESULT 0x{hr & 0xffffffff:08X}")
    return {"selector": selector, "payload": payload.hex(" "),
            "hresult": f"0x{hr & 0xffffffff:08X}", "bytes_returned": returned}

def friendly_name(moniker):
    bag_ptr = moniker.BindToStorage(None, None, byref(IPropertyBag._iid_))
    bag = bag_ptr.QueryInterface(IPropertyBag)
    try:
        return str(bag.Read("FriendlyName", pErrorLog=None))
    except Exception:
        return "(unknown)"

def find_ks_control(target_index: int):
    dev_enum = comtypes.CoCreateInstance(CLSID_SYSTEM_DEVICE_ENUM, interface=ICreateDevEnum)
    monikers = dev_enum.CreateClassEnumerator(CLSID_VIDEO_INPUT_CATEGORY, 0)
    if monikers is None:
        raise RuntimeError("No DirectShow camera devices found")
    idx = 0
    while True:
        try:
            moniker, fetched = monikers.Next(1)
        except Exception:
            break
        if not fetched or moniker is None:
            break
        name = friendly_name(moniker)
        if idx == target_index:
            base_filter = moniker.BindToObject(None, None, byref(IID_BASE_FILTER)).QueryInterface(IUnknown)
            topology = base_filter.QueryInterface(IKsTopologyInfo)
            count_ptr = wintypes.DWORD(0)
            # Native vtable slot: IUnknown(3) + five preceding topology methods.
            this_ptr = ctypes.cast(topology, c_void_p).value
            vt = ctypes.cast(this_ptr, POINTER(POINTER(c_void_p))).contents
            fn = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(wintypes.DWORD))(vt[8])
            hr = int(fn(this_ptr, byref(count_ptr)))
            if hr < 0:
                raise OSError(f"get_NumNodes HRESULT 0x{hr & 0xffffffff:08X}")
            for node_id in range(count_ptr.value):
                node_type = topology.get_NodeType(node_id)
                if node_type == KSNODETYPE_DEV_SPECIFIC:
                    instance = topology.CreateNodeInstance(node_id, byref(IID_KS_CONTROL))
                    return name, node_id, instance.QueryInterface(IKsControl)
            raise RuntimeError(f"{name}: HIK vendor-specific XU node not found")
        idx += 1
    raise RuntimeError(f"DirectShow camera index {target_index} not found")

def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--frames", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.frames <= 100:
        parser.error("--frames must be 1..100")

    report = {"camera_index": args.index, "requested_mode": 8, "restored": False,
              "pre_frames": [], "mode8_frames": [], "operations": []}
    cap = None
    ks = None
    node_id = None
    original_mode = None
    mode_change_attempted = False
    comtypes.CoInitialize()
    try:
        # Start the real UVC stream BEFORE requesting mode 8.
        cap = cv2.VideoCapture(args.index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            raise RuntimeError(f"DirectShow could not open camera index {args.index}")
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUY2"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 256)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 392)
        cap.set(cv2.CAP_PROP_FPS, 25.0)
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        for _ in range(5):
            ok, frame = cap.read()
            if not ok or frame is None:
                raise RuntimeError("Could not obtain five startup frames; mode was not changed")
            report["pre_frames"].append({"shape": list(frame.shape), "bytes": int(frame.nbytes)})
        report["stream_started"] = True

        name, node_id, ks = find_ks_control(args.index)
        report["device_name"] = name
        version = get_property(ks, node_id, 4)
        report["protocol_version_hex"] = version.hex(" ")
        if version.rstrip(b"\0") != b"2.0":
            raise RuntimeError(f"Unsupported XU protocol version: {version!r}")

        report["operations"].append(set_property(ks, node_id, 5, b"\x03\x05"))
        original = get_property(ks, node_id, 3)
        report["original_mode_payload_hex"] = original.hex(" ")
        if len(original) != 2 or original[0] != 1 or not 1 <= original[1] <= 11:
            raise RuntimeError(f"Unexpected original mode response: {original.hex(' ')}")
        original_mode = original[1]
        report["original_mode"] = original_mode
        if original_mode != 3:
            raise RuntimeError(f"Expected current mode 3 for this controlled test, got {original_mode}; no mode change made")

        # The stream is already running. Select the command again, then SET selector 3.
        report["operations"].append(set_property(ks, node_id, 5, b"\x03\x05"))
        mode_change_attempted = True
        report["operations"].append(set_property(ks, node_id, 3, b"\x01\x08"))
        confirmed = get_property(ks, node_id, 3)
        report["mode8_confirmation_hex"] = confirmed.hex(" ")
        if len(confirmed) < 2 or confirmed[0] != 1 or confirmed[1] != 8:
            raise RuntimeError(f"Camera did not confirm mode 8: {confirmed.hex(' ')}")
        report["mode8_confirmed"] = True

        for _ in range(args.frames):
            ok, frame = cap.read()
            if not ok or frame is None:
                report["frame_read_error"] = "DirectShow read returned no frame"
                break
            report["mode8_frames"].append({
                "shape": list(frame.shape), "bytes": int(frame.nbytes),
                "first16_hex": frame.tobytes()[:16].hex(" "),
            })
        report["mode8_frame_sizes"] = sorted({x["bytes"] for x in report["mode8_frames"]})
    finally:
        # Restore the saved mode while the stream is still active.
        if mode_change_attempted and ks is not None and node_id is not None and original_mode is not None:
            try:
                report["operations"].append(set_property(ks, node_id, 5, b"\x03\x05"))
                report["operations"].append(set_property(ks, node_id, 3, bytes((1, original_mode))))
                restored = get_property(ks, node_id, 3)
                report["restore_confirmation_hex"] = restored.hex(" ")
                report["restored"] = len(restored) >= 2 and restored[0] == 1 and restored[1] == original_mode
                if not report["restored"]:
                    report["restore_error"] = "Camera did not confirm original mode"
            except Exception as exc:
                report["restore_error"] = str(exc)
        if cap is not None:
            cap.release()
        comtypes.CoUninitialize()
        print(json.dumps(report, indent=2), flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"fatal_error": str(exc)}, indent=2), flush=True)
        raise
