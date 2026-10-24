#!/usr/bin/env python3
"""Recover a USB-connected C5 on Windows without replacing its drivers.

C5VRX by Twotoz and contributors: https://github.com/Twotoz/C5VRX
Official website / Discord invite: https://twotoz.github.io/C5VRX/

Uses installed WinUSB even when interface GUID registration is missing.
Selects only the explicit USB serial, validates both C5 JTAG IDs and DTM
version, and checks every DMI/SBA transfer. No flash/NVS/eFuse writes.
--reset-system stops the dump writer and triggers an RTC watchdog system
reset. This interrupts the running receiver. Use enter_download.py for a
healthy running C5VRX; this is a recovery path for an unresponsive console.

Protocol references (Espressif / upstream RISC-V):
https://github.com/espressif/openocd-esp32/blob/v0.12.0-esp32-20260424/src/jtag/drivers/esp_usb_jtag.c
ESP-IDF v6.0.2 components/soc/esp32c5/register/soc/lp_wdt_reg.h
RISC-V Debug Specification v0.13, DTM/DMI/system bus access.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import json
import os
import subprocess
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial',required=True,help='USB serial (C5 base MAC, colon-separated)')
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--inspect',action='store_true',help='read JTAG and recovery register state')
    action.add_argument('--reset-system',action='store_true',help='interrupt receiver with full-system reset')
    args=parser.parse_args()
    if os.name!='nt':parser.error('requires Windows and its installed WinUSB driver')
    # Enumerate read-only; never install drivers, edit registration or elevate.
    query=r"""Get-PnpDevice -PresentOnly -InstanceId 'USB\VID_303A&PID_1001&MI_02\*' | ForEach-Object {
        $d=$_; [pscustomobject]@{
            pdo=(Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName DEVPKEY_Device_PDOName).Data;
            parent=(Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName DEVPKEY_Device_Parent).Data;
            service=(Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName DEVPKEY_Device_Service).Data
        }
    } | ConvertTo-Json -Compress"""
    result=subprocess.run(['powershell.exe','-NoProfile','-Command',query],capture_output=True,text=True,check=True)
    devices=json.loads(result.stdout or '[]')
    if isinstance(devices,dict):devices=[devices]
    devices=[d for d in devices if d['parent'].upper().endswith('\\'+args.serial.upper()) and d['service'].upper()=='WINUSB']
    if len(devices)!=1:raise RuntimeError(f'expected one matching WinUSB C5, found {len(devices)}')
    pdo=devices[0]['pdo']
    print('Selected',devices[0]['parent'],pdo,flush=True)
    k=c.WinDLL('kernel32',use_last_error=True)
    u=c.WinDLL('winusb',use_last_error=True)
    k.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
    k.CreateFileW.restype=w.HANDLE
    k.CloseHandle.argtypes=[w.HANDLE]
    u.WinUsb_Initialize.argtypes=[w.HANDLE,c.POINTER(w.HANDLE)]
    u.WinUsb_Free.argtypes=[w.HANDLE]
    class Setup(c.Structure):
        _pack_=1
        _fields_=[('type',c.c_ubyte),('request',c.c_ubyte),('value',c.c_ushort),('index',c.c_ushort),('length',c.c_ushort)]
    u.WinUsb_ControlTransfer.argtypes=[w.HANDLE,Setup,c.c_void_p,w.ULONG,c.POINTER(w.ULONG),c.c_void_p]
    u.WinUsb_WritePipe.argtypes=[w.HANDLE,c.c_ubyte,c.c_void_p,w.ULONG,c.POINTER(w.ULONG),c.c_void_p]
    u.WinUsb_ReadPipe.argtypes=u.WinUsb_WritePipe.argtypes
    u.WinUsb_SetPipePolicy.argtypes=[w.HANDLE,c.c_ubyte,w.ULONG,w.ULONG,c.c_void_p]
    u.WinUsb_QueryInterfaceSettings.argtypes=[w.HANDLE,c.c_ubyte,c.c_void_p]
    u.WinUsb_QueryPipe.argtypes=[w.HANDLE,c.c_ubyte,c.c_ubyte,c.c_void_p]
    u.WinUsb_FlushPipe.argtypes=[w.HANDLE,c.c_ubyte]
    u.WinUsb_ResetPipe.argtypes=[w.HANDLE,c.c_ubyte]
    class Pipe(c.Structure):
        _fields_=[('kind',c.c_int),('ep',c.c_ubyte),('packet',c.c_ushort),('interval',c.c_ubyte)]
    h=k.CreateFileW('\\\\?\\GLOBALROOT'+pdo,0xC0000000,3,None,3,0x40000000,None)
    if h==w.HANDLE(-1).value:raise c.WinError(c.get_last_error())
    ih=w.HANDLE()
    try:
        if not u.WinUsb_Initialize(h,c.byref(ih)):raise c.WinError(c.get_last_error())
        def control(kind,req,value,length=0):
            buf=c.create_string_buffer(max(length,1));n=w.ULONG()
            if not u.WinUsb_ControlTransfer(ih,Setup(kind,req,value,0,length),buf,length,c.byref(n),None):raise c.WinError(c.get_last_error())
            print('control',hex(kind),req,hex(value),n.value,buf.raw[:n.value].hex(),flush=True)
        control(0x80,6,0x2000,255)
        desc=c.create_string_buffer(9)
        if not u.WinUsb_QueryInterfaceSettings(ih,0,desc):raise c.WinError(c.get_last_error())
        print('interface',desc.raw.hex(),flush=True)
        pipes=[]
        for index in range(desc.raw[4]):
            pipe=Pipe()
            if not u.WinUsb_QueryPipe(ih,0,index,c.byref(pipe)):raise c.WinError(c.get_last_error())
            print('pipe',pipe.kind,hex(pipe.ep),pipe.packet,flush=True)
            pipes.append(pipe.ep)
        ep_out=next(ep for ep in pipes if not ep&128);ep_in=next(ep for ep in pipes if ep&128)
        for ep in pipes:u.WinUsb_ResetPipe(ih,ep)
        u.WinUsb_FlushPipe(ih,ep_in)
        for ep in pipes:
            timeout=w.ULONG(2000)
            if not u.WinUsb_SetPipePolicy(ih,ep,3,4,c.byref(timeout)):raise c.WinError(c.get_last_error())
        timeout=w.ULONG(100);u.WinUsb_SetPipePolicy(ih,ep_in,3,4,c.byref(timeout))
        drain=c.create_string_buffer(64);drained=w.ULONG()
        for _ in range(100):
            if not u.WinUsb_ReadPipe(ih,ep_in,drain,64,c.byref(drained),None):break
        timeout=w.ULONG(2000);u.WinUsb_SetPipePolicy(ih,ep_in,3,4,c.byref(timeout))
        control(0x40,0,8)
        def clocks(commands):
            captured=sum(bool(x&4) for x in commands if x<8)
            commands=list(commands)+[10]
            if len(commands)%2:commands.append(10)
            data=bytes((commands[n]<<4)|commands[n+1] for n in range(0,len(commands),2))
            n=w.ULONG()
            if not u.WinUsb_WritePipe(ih,ep_out,data,len(data),c.byref(n),None):raise c.WinError(c.get_last_error())
            if n.value!=len(data):raise RuntimeError('short JTAG write')
            if not captured:return 0
            length=(captured+7)//8;buf=c.create_string_buffer(64)
            result=b''
            for attempt in range(5):
                if not u.WinUsb_ReadPipe(ih,ep_in,buf,64,c.byref(n),None):raise c.WinError(c.get_last_error())
                result+=buf.raw[:n.value]
                if len(result)>=length:break
            if len(result)!=length:raise RuntimeError(f'JTAG capture length {len(result)}, expected {length}')
            return int.from_bytes(result,'little')&((1<<captured)-1)
        clocks([2]*6+[0])
        def scan(value,bits,ir=False):
            cmd=[2,2,0,0] if ir else [2,0,0]
            cmd += [4|((value>>n)&1)|(2 if n==bits-1 else 0) for n in range(bits)]
            cmd += [2,0]+[0]*8
            return clocks(cmd)
        scan(0x21,10,True);ident=scan(0,64)
        print('JTAG IDCODE chain',hex(ident),flush=True)
        if ident!=0x00017c2500017c25:raise RuntimeError(f'unexpected C5 TAP chain {ident:016x}')
        # C5 has LP tap0 followed by HP tap1; leave LP in BYPASS.
        def hp_ir(value):return scan((value<<5)|31,10,True)
        def hp_dr(value,bits):return scan(value<<1,bits+1)>>1
        hp_ir(0x10);dtmcs=hp_dr(0,32)
        print('DTMCS',hex(dtmcs),flush=True)
        abits=(dtmcs>>4)&63
        if not (5<=abits<=12 and dtmcs&15==1):raise RuntimeError('unsupported DTM')
        hp_ir(0x11)
        def dmi(addr,data=0,op=1):
            hp_dr((addr<<34)|(data<<2)|op,abits+34)
            result=hp_dr(0,abits+34)
            if result&3:raise RuntimeError(f'DMI status {result&3} addr {addr:x}')
            return (result>>2)&0xffffffff
        dmi(0x10,1,2)
        print('DMSTATUS',hex(dmi(0x11)),'SBCS',hex(dmi(0x38)),flush=True)
        def memread(addr):
            dmi(0x38,0x140000,2);dmi(0x39,addr,2)
            value=dmi(0x3c);status=dmi(0x38)
            if status&0x607000:raise RuntimeError(f'SBA read failed {addr:x} {status:x}')
            return value
        def memwrite(addr,value):
            dmi(0x38,0x40000,2);dmi(0x39,addr,2);dmi(0x3c,value,2)
            status=dmi(0x38)
            if status&0x607000:raise RuntimeError(f'SBA write failed {addr:x} {status:x}')
        for addr in (0x600b1034,0x600b1038,0x600a9004,0x60095004):
            print('MEM',hex(addr),hex(memread(addr)),flush=True)
        if args.reset_system:
            dmi(0x10,0x80000001,2)
            try:
                if dmi(0x11)&0x300!=0x300:raise RuntimeError('CPU did not halt')
                memwrite(0x600a9004,memread(0x600a9004)&0x7fffffff)
                memwrite(0x60095004,memread(0x60095004)&~0x10f00)
                config=memread(0x600b1c00)
                memwrite(0x600b1c18,0x50d83aa1)
                memwrite(0x600b1c04,50000)
                memwrite(0x600b1c00,(config&0x7ffff)|0xc0000000)
            finally:
                dmi(0x10,0x40000001,2)
            print('RTC watchdog full-system reset armed',flush=True)
    finally:
        if ih:u.WinUsb_Free(ih)
        k.CloseHandle(h)



if __name__=='__main__':main()
