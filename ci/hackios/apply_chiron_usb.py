#!/usr/bin/env python3
"""Connect the phone loader to its existing public EDK2 Fastboot transport."""

import argparse
from pathlib import Path

from apply_chiron import replace_once


def apply(source):
    path = source / "xnuloader/loader.c"
    text = path.read_text()
    text = replace_once(
        text,
        "\tif (PLAT == &platform_chiron) {\n\t\tif (kernel_mpidr != this_mpidr || more_cores) {",
        "\tif (PLAT == &platform_chiron) {\n\t\tmore_cores = 0;\n\t\tif (kernel_mpidr != this_mpidr) {",
    )
    text = replace_once(text, "static usb_device_protocol *USB;", """static usb_device_protocol *USB;
struct chiron_usb_transport {
\tuint64_t revision;
\tefi_status (EFIAPI *start)(void);
\tefi_status (EFIAPI *receive)(uint64_t *size, void **buffer);
\tefi_status (EFIAPI *send)(uint64_t size, const void *buffer);
};
static struct chiron_usb_transport *CHIRON_USB;
static void *chiron_packet;
static uint64_t chiron_packet_size, chiron_packet_at;""")
    text = replace_once(text, "\t/* Ask the platform to bring the controller up",
                        """\tif (PLAT == &platform_chiron) {
\t\tstatic const efi_guid guid = {0x46037394,0xa01b,0x41fa,{0x83,0xcc,0xd8,0x45,0xd9,0x1c,0xae,0x70}};
\t\tefi_status status = BS->locate_protocol(&guid, NULL, (void **)&CHIRON_USB);
\t\tif (EFI_ERROR(status) || CHIRON_USB->revision != 1) {
\t\t\tprint("chiron: bootstrap USB transport missing\\n");
\t\t\treturn 0;
\t\t}
\t\tstatus = CHIRON_USB->start();
\t\tif (EFI_ERROR(status)) {
\t\t\tprint("chiron: EDK2 USB start failed %x\\n", status);
\t\t\treturn 0;
\t\t}
\t\tif (EFI_ERROR(BS->allocate_pool(EfiLoaderData, USB_CHUNK, (void **)&usb_rx_buf)) ||
\t\t    EFI_ERROR(BS->allocate_pool(EfiLoaderData, USB_CHUNK, (void **)&usb_tx_buf))) return 0;
\t\tusb_connected = 1;
\t\tprint("chiron: using existing EDK2 Fastboot USB transport\\n");
\t\treturn 1;
\t}
\t/* Ask the platform to bring the controller up""")
    text = replace_once(text, "\tif (usb_rx_queued) {", """\tif (CHIRON_USB != NULL) {
\t\tfor (;;) {
\t\t\tif (chiron_packet != NULL && chiron_packet_at < chiron_packet_size) {
\t\t\t\tuint64_t take = chiron_packet_size - chiron_packet_at;
\t\t\t\tif (take > len) take = len;
\t\t\t\tmemcpy(buf, (uint8_t *)chiron_packet + chiron_packet_at, (size_t)take);
\t\t\t\tchiron_packet_at += take;
\t\t\t\tif (chiron_packet_at == chiron_packet_size) {
\t\t\t\t\tBS->free_pool(chiron_packet);
\t\t\t\t\tchiron_packet = NULL;
\t\t\t\t}
\t\t\t\treturn (int64_t)take;
\t\t\t}
\t\t\tefi_status status = CHIRON_USB->receive(&chiron_packet_size, &chiron_packet);
\t\t\tchiron_packet_at = 0;
\t\t\tif (status == (EFI_ERROR_BIT | 6)) {
\t\t\t\tBS->stall(1000);
\t\t\t\tcontinue;
\t\t\t}
\t\t\tif (EFI_ERROR(status)) return -1;
\t\t\tif (chiron_packet_size == 0) {
\t\t\t\tif (chiron_packet != NULL) BS->free_pool(chiron_packet);
\t\t\t\tchiron_packet = NULL;
\t\t\t}
\t\t}
\t}
\tif (usb_rx_queued) {""")
    text = replace_once(text, "\tif (data != usb_tx_buf) {",
                        "\tif (CHIRON_USB != NULL) return EFI_ERROR(CHIRON_USB->send(len, data)) ? -1 : (int64_t)len;\n"
                        "\tif (data != usb_tx_buf) {")
    text = replace_once(text, 'if (str_eq(var, "product")) {',
                        'if (str_eq(var, "board")) {\n'
                        '\t\t\t\tfb_reply("OKAY", PLAT->name);\n'
                        '\t\t\t} else if (str_eq(var, "product")) {')
    text = replace_once(text, '\t\t\t\tfb_reply("OKAY", download_to_ramdisk ? "0xffffffff" : "0x03f00000");',
                        '\t\t\t\tif (download_to_ramdisk && PLAT == &platform_chiron) {\n'
                        '\t\t\t\t\tuint64_t mark = log_len;\n'
                        '\t\t\t\t\tuint64_t limit = win_size - RESIDENT_RD_OFFSET - RESIDENT_RD_RESERVE;\n'
                        '\t\t\t\t\tprint("0x%x", limit);\n'
                        '\t\t\t\t\tlog_buf[log_len] = 0;\n'
                        '\t\t\t\t\tfb_reply("OKAY", log_buf + mark);\n'
                        '\t\t\t\t\tlog_len = (uint32_t)mark;\n'
                        '\t\t\t\t} else {\n'
                        '\t\t\t\t\tfb_reply("OKAY", download_to_ramdisk ? "0xffffffff" : "0x03f00000");\n'
                        '\t\t\t\t}')
    path.write_text(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, required=True)
    apply(ap.parse_args().source)


if __name__ == "__main__":
    main()
