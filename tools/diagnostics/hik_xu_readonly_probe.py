"""Read-only HIKMICRO UVC Extension Unit diagnostic for Windows.

This script sends one documented command-selection SET (selector 5, payload 03 05), but never changes camera stream mode.
It enumerates DirectShow video devices, inspects KS topology, then attempts
a GET of selector 4, selects the selector-3 read command, then queries and reads selector 3.

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
READ_BUFFER_SIZE = 4096


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
        # Preserve the exact native vtable order from vidcap.h:
        # get_NodeName precedes get_NumNodes.
        COMMETHOD([], HRESULT, "get_NodeName", (["in"], wintypes.DWORD),
                  (["out"], c_void_p), (["in"], wintypes.DWORD),
                  (["out"], POINTER(wintypes.DWORD))),
        COMMETHOD([], HRESULT, "get_NumNodes", (["out"], POINTER(wintypes.DWORD))),
        COMMETHOD([], HRESULT, "get_NodeType", (["in"], wintypes.DWORD),
                  (["out"], POINTER(GUID))),
        # Native signature: CreateNodeInstance(DWORD nodeId, REFIID iid, void**).
        COMMETHOD([], HRESULT, "CreateNodeInstance", (["in"], wintypes.DWORD),
                  (["in"], POINTER(GUID)),
                  (["out"], POINTER(POINTER(IUnknown)))),
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


def _raw_ks_property(
    ks: IKsControl, prop: KSP_NODE, data_ptr: int | None, data_length: int
) -> tuple[int, int]:
    """Call IKsControl::KsProperty directly and preserve HRESULT + BytesReturned."""
    this_ptr = ctypes.cast(ks, c_void_p).value
    if not this_ptr:
        raise RuntimeError("IKsControl returned a null interface pointer")
    vtable = ctypes.cast(this_ptr, POINTER(POINTER(c_void_p))).contents
    call = ctypes.WINFUNCTYPE(
        ctypes.c_long, c_void_p, c_void_p, c_ulong,
        c_void_p, c_ulong, POINTER(c_ulong)
    )(vtable[3])
    returned = c_ulong(0)
    hr = int(call(
        this_ptr, ctypes.byref(prop), ctypes.sizeof(prop),
        data_ptr, data_length, byref(returned)
    ))
    return hr, int(returned.value)


def _set_xu_selector5_command(ks: IKsControl, node_id: int) -> dict:
    """Select the HTI UVC2 read-command channel; this is not a mode change."""
    payload = (ctypes.c_ubyte * 2)(0x03, 0x05)
    prop = KSP_NODE(
        HIK_XU_GUID, 5,
        0x00000002 | KSPROPERTY_TYPE_TOPOLOGY,
        node_id, 0,
    )
    hr, returned = _raw_ks_property(
        ks, prop, ctypes.cast(payload, c_void_p).value, ctypes.sizeof(payload)
    )
    return {
        "payload": "03 05",
        "hresult": f"0x{hr & 0xFFFFFFFF:08X}",
        "bytes_returned": returned,
        "ok": hr >= 0,
    }


def _read_xu_selector(ks: IKsControl, node_id: int, selector: int) -> dict:
    """Query the KS response size, then GET exactly that many bytes."""
    prop = KSP_NODE(
        HIK_XU_GUID,
        selector,
        KSPROPERTY_TYPE_GET | KSPROPERTY_TYPE_TOPOLOGY,
        node_id,
        0,
    )
    try:
        # IKsControl documents NULL/0 as a required-size query. Usbvideo
        # translates this property request using the XU control descriptor.
        size_hr, required = _raw_ks_property(ks, prop, None, 0)
        if not 1 <= required <= 512:
            return {
                "ok": False,
                "stage": "size_query",
                "hresult": f"0x{size_hr & 0xFFFFFFFF:08X}",
                "bytes_required": required,
                "error": "KS did not report a plausible response size (1..512)",
            }
        output = ctypes.create_string_buffer(required)
        hr, returned = _raw_ks_property(
            ks, prop, ctypes.cast(output, c_void_p).value, required
        )
        if hr < 0:
            return {
                "ok": False,
                "stage": "GET",
                "hresult": f"0x{hr & 0xFFFFFFFF:08X}",
                "bytes_required": required,
                "bytes_returned": returned,
            }
        payload = output.raw[: min(returned, required)]
        return {
            "ok": True,
            "size_query_hresult": f"0x{size_hr & 0xFFFFFFFF:08X}",
            "bytes_required": required,
            "bytes_returned": returned,
            "hex": payload.hex(" "),
            "ascii": "".join(chr(b) if 32 <= b < 127 else "." for b in payload),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _raw_get_num_nodes(topology: IKsTopologyInfo) -> int:
    """Call IKsTopologyInfo::get_NumNodes through its native vtable slot.

    This bypasses comtypes' generated out-parameter wrapper while preserving
    the documented COM ABI. IKsTopologyInfo::get_NumNodes is vtable slot 8:
    IUnknown's 3 slots plus the first 5 IKsTopologyInfo methods.
    """
    this_ptr = ctypes.cast(topology, c_void_p).value
    if not this_ptr:
        raise RuntimeError("IKsTopologyInfo returned a null interface pointer")
    vtable = ctypes.cast(
        this_ptr, POINTER(POINTER(c_void_p))
    ).contents
    get_num_nodes = ctypes.WINFUNCTYPE(
        ctypes.c_long, c_void_p, POINTER(wintypes.DWORD)
    )(vtable[8])
    count = wintypes.DWORD(0)
    hr = int(get_num_nodes(this_ptr, byref(count)))
    if hr < 0:
        raise OSError(f"IKsTopologyInfo::get_NumNodes failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")
    return int(count.value)


def inspect_device(moniker: IMoniker, index: int) -> dict:
    item: dict = {"index": index, "name": _friendly_name(moniker)}
    stage = "BindToObject(IBaseFilter)"
    try:
        filter_ptr = moniker.BindToObject(None, None, byref(IID_BASE_FILTER))
        stage = "QueryInterface(IUnknown)"
        base_filter = filter_ptr.QueryInterface(IUnknown)
        stage = "QueryInterface(IKsTopologyInfo)"
        topology = base_filter.QueryInterface(IKsTopologyInfo)
        stage = "IKsTopologyInfo.get_NumNodes (native vtable)"
        node_count = _raw_get_num_nodes(topology)
        nodes = []
        for node_id in range(node_count):
            try:
                stage = f"IKsTopologyInfo.get_NodeType(node_id={node_id})"
                node_type = topology.get_NodeType(node_id)
                node_entry = {"node_id": node_id, "node_type": str(node_type)}
                if node_type == KSNODETYPE_DEV_SPECIFIC:
                    try:
                        # IKsControl is obtained from the topology node instance,
                        # not assumed to be implemented by the parent filter.
                        node_instance = topology.CreateNodeInstance(
                            node_id, byref(IID_KS_CONTROL)
                        )
                        ks = node_instance.QueryInterface(IKsControl)
                        node_entry["xu_selector4_get"] = _read_xu_selector(
                            ks, node_id, XU_PROTOCOL_VERSION_SELECTOR
                        )
                        # HTI UVC2 protocol: select the read-command channel,
                        # then query/read selector 3. This SET does not select
                        # or change the thermal stream mode.
                        node_entry["xu_selector5_command_select"] = (
                            _set_xu_selector5_command(ks, node_id)
                        )
                        if node_entry["xu_selector5_command_select"]["ok"]:
                            node_entry["xu_selector3_get"] = _read_xu_selector(
                                ks, node_id, 3
                            )
                        else:
                            node_entry["xu_selector3_get"] = {
                                "ok": False,
                                "stage": "command_select",
                                "error": "Selector 5 command selection failed",
                            }
                        node_entry["ikSControl_available"] = True
                    except Exception as exc:
                        node_entry["ikSControl_available"] = False
                        node_entry["xu_selector4_get"] = {
                            "ok": False, "error": str(exc)
                        }
                nodes.append(node_entry)
            except Exception as exc:
                nodes.append({"node_id": node_id, "error": str(exc)})

        item["topology_available"] = True
        item["node_count"] = node_count
        item["nodes"] = nodes
    except Exception as exc:
        item["error_stage"] = stage
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
        "operation": "GET selector 4; SET selector 5 payload 03 05 (read-command selection only); size-query + GET selector 3; no stream mode change",
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
            for node in dev.get("nodes", []):
                print(f"  Node {node['node_id']}: {node.get('node_type', node.get('error'))}")
                if "ikSControl_available" in node:
                    print("    Node IKsControl:", node["ikSControl_available"])
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
