"""Read-only HIKMICRO UVC Extension Unit diagnostic for Windows.

This script DOES NOT send SET requests and never changes camera mode.
It enumerates DirectShow video devices, inspects KS topology, then attempts
a GET-only read of XU selector 4 on each device-specific node.

Requires Windows, Python 3.10+, and: pip install comtypes
Run with HIKMICRO Analyzer closed and DriverThermalQC disconnected.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import platform
import sys
from ctypes import POINTER, byref, c_void_p, c_ulong, wintypes

if sys.platform != "win32":
    raise SystemExit("This diagnostic is Windows-only.")

try:
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
    from comtypes.persist import IPropertyBag
except ImportError as exc:
    raise SystemExit(
        "Missing optional dependency. Install it with: python -m pip install comtypes"
    ) from exc


# DirectShow / KS identifiers.
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

KSPROPERTY_TYPE_GET = 0x00000001
KSPROPERTY_TYPE_TOPOLOGY = 0x10000000
XU_PROTOCOL_VERSION_SELECTOR = 4
READ_BUFFER_SIZE = 64


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
    _methods_ = [
        COMMETHOD([], HRESULT, "Next", (["in"], c_ulong),
                  (["out"], POINTER(POINTER(IMoniker))),
                  (["out"], POINTER(c_ulong))),
    ]


class ICreateDevEnum(IUnknown):
    _iid_ = IID_CREATE_DEV_ENUM
    _methods_ = [
        COMMETHOD([], HRESULT, "CreateClassEnumerator", (["in"], POINTER(GUID)),
                  (["out"], POINTER(POINTER(IEnumMoniker))),
                  (["in"], wintypes.DWORD)),
    ]


class IKsControl(IUnknown):
    _iid_ = IID_KS_CONTROL
    _methods_ = [
        COMMETHOD([], HRESULT, "KsProperty",
                  (["in"], c_void_p), (["in"], c_ulong),
                  (["in"], c_void_p), (["in"], c_ulong),
                  (["out"], POINTER(c_ulong))),
    ]


class IKsTopologyInfo(IUnknown):
    _iid_ = IID_KS_TOPOLOGY_INFO
    _methods_ = [
        COMMETHOD([], HRESULT, "get_NumCategories", (["out"], POINTER(wintypes.DWORD))),
        COMMETHOD([], HRESULT, "get_Category", (["in"], wintypes.DWORD),
                  (["out"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "get_NumConnections", (["out"], POINTER(wintypes.DWORD))),
        COMMETHOD([], HRESULT, "get_ConnectionInfo", (["in"], wintypes.DWORD), (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "get_NumNodes", (["out"], POINTER(wintypes.DWORD))),
        COMMETHOD([], HRESULT, "get_NodeType", (["in"], wintypes.DWORD),
                  (["out"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "CreateNodeInstance", (["in"], wintypes.DWORD),
                  (["in"], wintypes.DWORD), (["in"], POINTER(GUID)),
                  (["out"], POINTER(POINTER(IUnknown)))),
        COMMETHOD([], HRESULT, "get_NodeName", (["in"], wintypes.DWORD),
                  (["out"], c_void_p), (["in"], wintypes.DWORD),
                  (["out"], POINTER(wintypes.DWORD))),
    ]


class KSP_NODE(ctypes.Structure):
    _fields_ = [
        ("Set", GUID),
        ("Id", c_ulong),
        ("Flags", c_ulong),
        ("NodeId", c_ulong),
        ("Reserved", c_ulong),
    ]


def _friendly_name(moniker: IMoniker) -> str:
    bag_ptr = moniker.BindToStorage(None, None, byref(IPropertyBag._iid_))
    bag = bag_ptr.QueryInterface(IPropertyBag)
    try:
        value = bag.Read("FriendlyName", pErrorLog=None)
        return str(value)
    except Exception:
        return "(FriendlyName unavailable)"


def _read_xu_selector4(ks: IKsControl, node_id: int) -> dict:
    """Issue exactly one KSPROPERTY GET; no SET or mode change is possible."""
    prop = KSP_NODE(
        HIK_XU_GUID,
        XU_PROTOCOL_VERSION_SELECTOR,
        KSPROPERTY_TYPE_GET | KSPROPERTY_TYPE_TOPOLOGY,
        node_id,
        0,
    )
    output = ctypes.create_string_buffer(READ_BUFFER_SIZE)
    returned = c_ulong(0)
    try:
        ks.KsProperty(
            ctypes.addressof(prop), ctypes.sizeof(prop),
            ctypes.addressof(output), READ_BUFFER_SIZE, byref(returned),
        )
        payload = output.raw[: min(returned.value, READ_BUFFER_SIZE)]
        return {
            "ok": True,
            "bytes_returned": returned.value,
            "hex": payload.hex(" "),
            "ascii": "".join(chr(b) if 32 <= b < 127 else "." for b in payload),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def inspect_device(moniker: IMoniker, index: int) -> dict:
    item: dict = {"index": index, "name": _friendly_name(moniker)}
    try:
        filter_ptr = moniker.BindToObject(None, None, byref(IID_BASE_FILTER))
        base_filter = filter_ptr.QueryInterface(IUnknown)
        topology = base_filter.QueryInterface(IKsTopologyInfo)
        try:
            ks = base_filter.QueryInterface(IKsControl)
        except Exception as exc:
            item["ikSControl"] = {"available": False, "error": str(exc)}
            return item

        node_count = int(topology.get_NumNodes())
        nodes = []
        for node_id in range(node_count):
            try:
                node_type = topology.get_NodeType(node_id)
                node_entry = {"node_id": node_id, "node_type": str(node_type)}
                if node_type == KSNODETYPE_DEV_SPECIFIC:
                    node_entry["xu_selector4_get"] = _read_xu_selector4(ks, node_id)
                nodes.append(node_entry)
            except Exception as exc:
                nodes.append({"node_id": node_id, "error": str(exc)})

        item["ikSControl"] = {"available": True}
        item["node_count"] = node_count
        item["nodes"] = nodes
    except Exception as exc:
        item["error"] = str(exc)
    return item


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only HIKMICRO UVC XU probe")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()

    report = {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "xu_guid": str(HIK_XU_GUID),
        "operation": "GET selector 4 only; no SET requests are issued",
        "devices": [],
    }

    comtypes.CoInitialize()
    try:
        dev_enum = comtypes.CoCreateInstance(
            CLSID_SYSTEM_DEVICE_ENUM, interface=ICreateDevEnum
        )
        monikers = dev_enum.CreateClassEnumerator(CLSID_VIDEO_INPUT_CATEGORY, 0)
        if monikers is None:
            report["error"] = "No DirectShow video input devices were enumerated."
        else:
            index = 0
            while True:
                try:
                    moniker, fetched = monikers.Next(1)
                except Exception:
                    break
                if not fetched or moniker is None:
                    break
                report["devices"].append(inspect_device(moniker, index))
                index += 1
    finally:
        comtypes.CoUninitialize()

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("HIKMICRO UVC Extension Unit - READ-ONLY diagnostic")
        print("No SET requests were sent; camera mode was not changed.")
        print(f"Python: {report['python']}")
        print(f"Extension Unit GUID: {report['xu_guid']}")
        if report.get("error"):
            print("ERROR:", report["error"])
        for dev in report["devices"]:
            print(f"\n[{dev['index']}] {dev.get('name', '(unknown)')}")
            if "error" in dev:
                print("  Device inspection error:", dev["error"])
                continue
            print("  IKsControl:", dev.get("ikSControl", {}).get("available", False))
            for node in dev.get("nodes", []):
                print(f"  Node {node['node_id']}: {node.get('node_type', node.get('error'))}")
                read = node.get("xu_selector4_get")
                if read:
                    if read.get("ok"):
                        print(f"    Selector 4 GET: {read['bytes_returned']} bytes; {read['hex']}")
                        print(f"    ASCII: {read['ascii']}")
                    else:
                        print(f"    Selector 4 GET failed: {read['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
