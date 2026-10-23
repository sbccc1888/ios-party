#!/usr/bin/env python3
"""Read actual QEMU system registers through the already-used UniXNU GDB client."""

import json
from pathlib import Path
import socket
import sys
import xml.etree.ElementTree as ET


def capture(helper, destination):
    sys.path.insert(0, str(helper / "scripts/qemu-entry"))
    from gdb_packets import Packets
    wanted = {
        "TCR_EL1", "SCTLR_EL1", "ID_AA64MMFR0_EL1", "ID_AA64MMFR1_EL1",
        "ID_AA64ISAR0_EL1", "MIDR_EL1", "MPIDR_EL1",
    }
    aliases = {"MIDR": "MIDR_EL1", "SCTLR": "SCTLR_EL1", "MPIDR": "MPIDR_EL1"}
    with socket.create_connection(("127.0.0.1", 1234), timeout=5) as connection:
        packet = Packets(connection).request
        packet("qSupported:qXfer:features:read+")
        try:
            packet("?")
            def feature(name):
                text = ""
                offset = 0
                for _ in range(256):
                    chunk = packet(f"qXfer:features:read:{name}:{offset:x},1000")
                    if not chunk or chunk[0] not in "ml":
                        raise RuntimeError(f"missing actual QEMU register description {name}")
                    text += chunk[1:]
                    offset += len(chunk) - 1
                    if chunk[0] == "l":
                        if "xi:include" in text and "xmlns:xi=" not in text:
                            text = text.replace("<target", '<target xmlns:xi="http://www.w3.org/2001/XInclude"', 1)
                        return ET.fromstring(text)
                raise RuntimeError("register description exceeds bound")
            regs = {}
            for include in feature("target.xml").findall("{http://www.w3.org/2001/XInclude}include"):
                for register in feature(include.attrib["href"]).findall("reg"):
                    name = aliases.get(register.attrib["name"], register.attrib["name"])
                    if name in wanted:
                        raw = packet(f'p{int(register.attrib["regnum"]):x}')
                        regs[name] = hex(int.from_bytes(bytes.fromhex(raw), "little"))
            required = wanted - {"MPIDR_EL1", "MIDR_EL1"}
            destination.write_text(json.dumps({"system_registers": regs, "qemu_only": True}, indent=2) + "\n")
            if not required <= regs.keys():
                raise RuntimeError(f"missing system registers: {required - regs.keys()}")
        finally:
            packet("D")
