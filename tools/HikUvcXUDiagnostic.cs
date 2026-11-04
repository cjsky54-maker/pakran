using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

namespace HikUvcDiagnostic
{
    [ComImport, Guid("29840822-5B84-11D0-BD3B-00A0C911CE86"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface ICreateDevEnum
    {
        [PreserveSig]
        int CreateClassEnumerator(ref Guid category, out IEnumMoniker enumerator, int flags);
    }

    [ComImport, Guid("720D4AC0-7533-11D0-A5D6-28DB04C10000"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IKsTopologyInfo
    {
        [PreserveSig] int get_NumCategories(out uint count);
        [PreserveSig] int get_Category(uint index, out Guid category);
        [PreserveSig] int get_NumConnections(out uint count);
        [PreserveSig] int get_ConnectionInfo(uint index, IntPtr connectionInfo);
        [PreserveSig] int get_NumNodes(out uint count);
        [PreserveSig] int get_NodeType(uint nodeId, out Guid nodeType);
        [PreserveSig] int get_NodeName(uint nodeId, out IntPtr nodeName);
        [PreserveSig] int CreateNodeInstance(uint nodeId, ref Guid iid,
            [MarshalAs(UnmanagedType.IUnknown)] out object node);
    }

    [ComImport, Guid("28F54685-06FD-11D2-B27A-00A0C9223196"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IKsControl
    {
        [PreserveSig]
        int KsProperty(IntPtr property, uint propertyLength, IntPtr data,
            uint dataLength, out uint bytesReturned);
        [PreserveSig]
        int KsMethod(IntPtr method, uint methodLength, IntPtr data,
            uint dataLength, out uint bytesReturned);
        [PreserveSig]
        int KsEvent(IntPtr eventData, uint eventLength, IntPtr data,
            uint dataLength, out uint bytesReturned);
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct KspNode
    {
        public Guid Set;
        public uint Id;
        public uint Flags;
        public uint NodeId;
        public uint Reserved;
    }

    public static class Runner
    {
        private static readonly Guid VideoInputCategory =
            new Guid("860BB310-5D01-11D0-BD3B-00A0C911CE86");
        private static readonly Guid DevSpecificNode =
            new Guid("941C7AC0-C559-11D0-8A2B-00A0C9255AC1");
        private static readonly Guid HikExtensionUnit =
            new Guid("A29E7641-DE04-47E3-8B2B-F4341AFF003B");

        private const uint Get = 0x00000001;
        private const uint Set = 0x00000002;
        private const uint Topology = 0x10000000;

        private static string Hex(byte[] bytes)
        {
            return BitConverter.ToString(bytes).Replace("-", " ");
        }

        private static byte[] ReadControl(IKsControl ks, uint nodeId, uint selector)
        {
            KspNode property = new KspNode {
                Set = HikExtensionUnit, Id = selector,
                Flags = Get | Topology, NodeId = nodeId, Reserved = 0
            };
            int size = Marshal.SizeOf(typeof(KspNode));
            IntPtr pProperty = Marshal.AllocHGlobal(size);
            try
            {
                Marshal.StructureToPtr(property, pProperty, false);
                uint needed;
                int hr = ks.KsProperty(pProperty, (uint)size, IntPtr.Zero, 0, out needed);
                if (needed == 0)
                    throw new InvalidOperationException(
                        "GET selector " + selector + " length query failed: HRESULT 0x" +
                        hr.ToString("X8"));
                if (needed > 4096)
                    throw new InvalidOperationException("Unexpected control length " + needed);

                IntPtr pData = Marshal.AllocHGlobal((int)needed);
                try
                {
                    for (int i = 0; i < (int)needed; i++) Marshal.WriteByte(pData, i, 0);
                    uint received;
                    hr = ks.KsProperty(pProperty, (uint)size, pData, needed, out received);
                    if (hr < 0)
                        throw new InvalidOperationException(
                            "GET selector " + selector + " failed: HRESULT 0x" + hr.ToString("X8"));
                    if (received == 0 || received > needed)
                        throw new InvalidOperationException(
                            "GET selector " + selector + " returned " + received + " bytes");
                    byte[] result = new byte[received];
                    Marshal.Copy(pData, result, 0, (int)received);
                    return result;
                }
                finally { Marshal.FreeHGlobal(pData); }
            }
            finally { Marshal.FreeHGlobal(pProperty); }
        }

        private static void SelectReadCommand(IKsControl ks, uint nodeId)
        {
            KspNode property = new KspNode {
                Set = HikExtensionUnit, Id = 5,
                Flags = Set | Topology, NodeId = nodeId, Reserved = 0
            };
            int size = Marshal.SizeOf(typeof(KspNode));
            IntPtr pProperty = Marshal.AllocHGlobal(size);
            IntPtr pData = Marshal.AllocHGlobal(2);
            try
            {
                Marshal.StructureToPtr(property, pProperty, false);
                Marshal.WriteByte(pData, 0, 0x03);
                Marshal.WriteByte(pData, 1, 0x05);
                uint received;
                int hr = ks.KsProperty(pProperty, (uint)size, pData, 2, out received);
                if (hr < 0)
                    throw new InvalidOperationException(
                        "Command-select SET selector 5 failed: HRESULT 0x" + hr.ToString("X8"));
            }
            finally
            {
                Marshal.FreeHGlobal(pData);
                Marshal.FreeHGlobal(pProperty);
            }
        }

        public static string Run(bool readMode)
        {
            List<string> lines = new List<string>();
            object deviceEnumerator = null;
            IEnumMoniker monikers = null;
            try
            {
                Type enumType = Type.GetTypeFromCLSID(
                    new Guid("62BE5D10-60EB-11D0-BD3B-00A0C911CE86"), true);
                deviceEnumerator = Activator.CreateInstance(enumType);
                ICreateDevEnum create = (ICreateDevEnum)deviceEnumerator;
                Guid category = VideoInputCategory;
                int enumHr = create.CreateClassEnumerator(ref category, out monikers, 0);
                if (enumHr != 0 || monikers == null)
                    return "No DirectShow video-input devices were enumerated.";

                IMoniker moniker;
                IntPtr fetched = Marshal.AllocHGlobal(sizeof(int));
                try
                {
                    while (monikers.Next(1, out moniker, fetched) == 0)
                    {
                        object filter = null;
                        object nodeObject = null;
                        try
                        {
                            Guid iidFilter = new Guid("56A86895-0AD4-11CE-B03A-0020AF0BA770");
                            filter = moniker.BindToObject(null, null, ref iidFilter);
                            IKsTopologyInfo topology = filter as IKsTopologyInfo;
                            if (topology == null) continue;

                            uint count;
                            if (topology.get_NumNodes(out count) < 0) continue;
                            for (uint nodeId = 0; nodeId < count; nodeId++)
                            {
                                Guid nodeType;
                                if (topology.get_NodeType(nodeId, out nodeType) < 0 ||
                                    nodeType != DevSpecificNode) continue;

                                Guid iidUnknown = new Guid("00000000-0000-0000-C000-000000000046");
                                if (topology.CreateNodeInstance(nodeId, ref iidUnknown, out nodeObject) < 0 ||
                                    nodeObject == null) continue;
                                IKsControl ks = nodeObject as IKsControl;
                                if (ks == null) { Marshal.ReleaseComObject(nodeObject); nodeObject = null; continue; }

                                // Windows' standard XU-info property is used to identify
                                // the descriptor GUID. It is queried on this dev-specific node.
                                byte[] descriptor = ReadStandardXuInfo(ks, nodeId);
                                if (descriptor == null || descriptor.Length < 17) continue;
                                Guid descriptorGuid = new Guid(new byte[] {
                                    descriptor[0], descriptor[1], descriptor[2], descriptor[3],
                                    descriptor[4], descriptor[5], descriptor[6], descriptor[7],
                                    descriptor[8], descriptor[9], descriptor[10], descriptor[11],
                                    descriptor[12], descriptor[13], descriptor[14], descriptor[15] });
                                if (descriptorGuid != HikExtensionUnit) continue;

                                lines.Add("MATCHED HIKMICRO XU");
                                lines.Add("Moniker: " + SafeDisplayName(moniker));
                                lines.Add("Topology node: " + nodeId);
                                lines.Add("XU GUID: " + descriptorGuid);
                                lines.Add("Descriptor bytes: " + Hex(descriptor));
                                lines.Add("UVC control count: " + descriptor[16]);

                                if (readMode)
                                {
                                    SelectReadCommand(ks, nodeId);
                                    byte[] protocol = ReadControl(ks, nodeId, 4);
                                    byte[] mode = ReadControl(ks, nodeId, 3);
                                    lines.Add("Protocol selector 4: " + Hex(protocol));
                                    lines.Add("Protocol ASCII: " + Encoding.ASCII.GetString(protocol).TrimEnd('\0'));
                                    lines.Add("Selector 3 current payload: " + Hex(mode));
                                    if (mode.Length >= 2)
                                        lines.Add("Channel=" + mode[0] + ", mode=" + mode[1]);
                                    lines.Add("No stream-mode setting was changed.");
                                }
                                else
                                {
                                    lines.Add("Read-only descriptor scan only. Re-run with -ReadMode to read selectors 4 and 3.");
                                }
                            }
                        }
                        catch (Exception ex)
                        {
                            lines.Add("Device/node diagnostic: " + ex.Message);
                        }
                        finally
                        {
                            if (nodeObject != null && Marshal.IsComObject(nodeObject))
                                Marshal.ReleaseComObject(nodeObject);
                            if (filter != null && Marshal.IsComObject(filter))
                                Marshal.ReleaseComObject(filter);
                            if (Marshal.IsComObject(moniker)) Marshal.ReleaseComObject(moniker);
                        }
                    }
                }
                finally { Marshal.FreeHGlobal(fetched); }
                if (lines.Count == 0)
                    lines.Add("No matching HIKMICRO Extension Unit was found in the DirectShow video topology.");
            }
            catch (Exception ex)
            {
                lines.Add("Diagnostic failed: " + ex.ToString());
            }
            finally
            {
                if (monikers != null && Marshal.IsComObject(monikers)) Marshal.ReleaseComObject(monikers);
                if (deviceEnumerator != null && Marshal.IsComObject(deviceEnumerator))
                    Marshal.ReleaseComObject(deviceEnumerator);
            }
            return String.Join(Environment.NewLine, lines.ToArray());
        }

        private static string SafeDisplayName(IMoniker moniker)
        {
            try
            {
                IBindCtx context;
                CreateBindCtx(0, out context);
                string name;
                moniker.GetDisplayName(context, null, out name);
                Marshal.ReleaseComObject(context);
                return name;
            }
            catch { return "(display name unavailable)"; }
        }

        private static byte[] ReadStandardXuInfo(IKsControl ks, uint nodeId)
        {
            // PROPSETID_VIDCAP_EXTENSION_UNIT, KSPROPERTY_EXTENSION_UNIT_INFO=0.
            Guid standardSet = new Guid("9B496CE1-811B-11CF-8C77-00AA00A14F7F");
            KspNode property = new KspNode {
                Set = standardSet, Id = 0, Flags = Get | Topology,
                NodeId = nodeId, Reserved = 0
            };
            int size = Marshal.SizeOf(typeof(KspNode));
            IntPtr pProperty = Marshal.AllocHGlobal(size);
            try
            {
                Marshal.StructureToPtr(property, pProperty, false);
                uint needed;
                int hr = ks.KsProperty(pProperty, (uint)size, IntPtr.Zero, 0, out needed);
                if (needed < 17 || needed > 512) return null;
                IntPtr pData = Marshal.AllocHGlobal((int)needed);
                try
                {
                    uint received;
                    hr = ks.KsProperty(pProperty, (uint)size, pData, needed, out received);
                    if (hr < 0 || received < 17) return null;
                    byte[] data = new byte[received];
                    Marshal.Copy(pData, data, 0, (int)received);
                    return data;
                }
                finally { Marshal.FreeHGlobal(pData); }
            }
            finally { Marshal.FreeHGlobal(pProperty); }
        }

        [DllImport("ole32.dll", PreserveSig = true)]
        private static extern int CreateBindCtx(uint reserved, out IBindCtx ppbc);
    }
}