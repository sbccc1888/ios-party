#!/usr/bin/env python3
"""Read the pinned RAM FAT32 volume and replace files without allocation or resizing."""

import struct


class RamFat32:
    def __init__(self, data):
        self.data = bytes(data)
        if (
            len(data) < 512 or data[510:512] != b"\x55\xaa"
            or data[11:13] != b"\x00\x02" or data[16] != 2
            or data[17:19] != b"\0\0" or data[82:90] != b"FAT32   "
            or struct.unpack_from("<I", data, 32)[0] * 512 != len(data)
        ):
            raise ValueError("expected the 512-byte-sector FAT32 RAM volume")
        spc = data[13]
        reserved = struct.unpack_from("<H", data, 14)[0]
        sectors = struct.unpack_from("<I", data, 36)[0]
        if not spc or spc & (spc - 1) or not reserved or not sectors:
            raise ValueError("invalid FAT32 allocation geometry")
        self.cluster_bytes = spc * 512
        self.fat_start = reserved * 512
        self.fat_bytes = sectors * 512
        self.data_start = self.fat_start + 2 * self.fat_bytes
        self.cluster_count = (len(data) - self.data_start) // self.cluster_bytes
        self.root = struct.unpack_from("<I", data, 44)[0]
        if (
            self.data_start >= len(data) or self.cluster_count <= 0
            or (self.cluster_count + 2) * 4 > self.fat_bytes
            or self.data[self.fat_start:self.fat_start + self.fat_bytes]
            != self.data[self.fat_start + self.fat_bytes:self.data_start]
        ):
            raise ValueError("FAT tables do not match or cannot cover the data area")

    def chain(self, first):
        visited = set()
        cluster = first
        while 2 <= cluster < 0x0ffffff8:
            if cluster >= self.cluster_count + 2 or cluster in visited or cluster == 0x0ffffff7:
                raise ValueError("invalid or cyclic FAT chain")
            visited.add(cluster)
            yield self.data_start + (cluster - 2) * self.cluster_bytes
            cluster = struct.unpack_from("<I", self.data, self.fat_start + cluster * 4)[0] & 0x0fffffff
        if cluster < 0x0ffffff8:
            raise ValueError("FAT chain did not terminate with EOC")

    @staticmethod
    def short_checksum(name):
        value = 0
        for byte in name:
            value = (((value & 1) << 7) + (value >> 1) + byte) & 255
        return value

    def entries(self, first):
        names = {}
        checksum = None
        for cluster in self.chain(first):
            for offset in range(cluster, cluster + self.cluster_bytes, 32):
                entry = self.data[offset:offset + 32]
                if entry[0] == 0:
                    return
                if entry[0] == 0xe5:
                    names, checksum = {}, None
                    continue
                if entry[11] == 0x0f:
                    ordinal = entry[0] & 31
                    if not 1 <= ordinal <= 20:
                        raise ValueError("invalid FAT long-name ordinal")
                    if entry[0] & 0x40:
                        names, checksum = {}, entry[13]
                    if checksum != entry[13] or ordinal in names:
                        raise ValueError("invalid FAT long-name chain")
                    value = entry[1:11] + entry[14:26] + entry[28:32]
                    names[ordinal] = value.decode("utf-16le").split("\0")[0].rstrip("\uffff")
                    continue
                if names:
                    if sorted(names) != list(range(1, max(names) + 1)) or checksum != self.short_checksum(entry[:11]):
                        raise ValueError("FAT long name does not match its short entry")
                    name = "".join(names[index] for index in sorted(names))
                else:
                    stem = entry[:8].decode("ascii").rstrip()
                    extension = entry[8:11].decode("ascii").rstrip()
                    name = stem + ("." + extension if extension else "")
                names, checksum = {}, None
                if entry[11] & 8 or name in (".", ".."):
                    continue
                first = (struct.unpack_from("<H", entry, 20)[0] << 16) | struct.unpack_from("<H", entry, 26)[0]
                yield {
                    "name": name, "attributes": entry[11], "first": first,
                    "bytes": struct.unpack_from("<I", entry, 28)[0], "entry_offset": offset,
                }

    def find(self, path):
        parts = path.strip("/").split("/")
        if not parts or any(part in ("", ".", "..") for part in parts):
            raise ValueError("expected an absolute volume-relative file path")
        cluster = self.root
        for index, part in enumerate(parts):
            matches = [entry for entry in self.entries(cluster)
                       if entry["name"].casefold() == part.casefold()]
            if len(matches) != 1:
                raise ValueError(f"missing or ambiguous FAT path component: {part}")
            entry = matches[0]
            if index == len(parts) - 1:
                if entry["attributes"] & 16:
                    raise ValueError("path is a directory")
                return entry
            if not entry["attributes"] & 16:
                raise ValueError("path component is not a directory")
            cluster = entry["first"]
        raise ValueError("missing file")

    def layout(self, path):
        entry = self.find(path)
        clusters = list(self.chain(entry["first"])) if entry["bytes"] else []
        if len(clusters) * self.cluster_bytes < entry["bytes"]:
            raise ValueError("file exceeds its allocated chain")
        return entry, clusters

    def read(self, path):
        entry, clusters = self.layout(path)
        return b"".join(self.data[offset:offset + self.cluster_bytes] for offset in clusters)[:entry["bytes"]]

    def replace_same_size(self, path, replacement):
        entry, clusters = self.layout(path)
        if len(replacement) != entry["bytes"]:
            raise ValueError("RAM file replacement must keep the exact file size")
        result = bytearray(self.data)
        cursor = 0
        for offset in clusters:
            count = min(self.cluster_bytes, len(replacement) - cursor)
            if count <= 0:
                break
            result[offset:offset + count] = replacement[cursor:cursor + count]
            cursor += count
        if cursor != len(replacement):
            raise ValueError("replacement did not fit the original chain")
        updated = RamFat32(result)
        if updated.read(path) != replacement or updated.data[:self.data_start] != self.data[:self.data_start]:
            raise ValueError("replacement altered allocation metadata or file contents")
        return updated
