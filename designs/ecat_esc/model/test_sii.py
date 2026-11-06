"""The SII image: its layout as SOEM reads it, and the generated ROM."""
import os
import struct
import unittest

import sii

HERE = os.path.dirname(os.path.abspath(__file__))
ROM_V = os.path.join(HERE, "..", "rtl", "sii_rom.v")


def word(n):
    return struct.unpack_from("<H", sii.image(), 2 * n)[0]


def bit_serial_crc8(data):
    crc = 0xFF
    for byte in data:
        for i in range(7, -1, -1):
            feedback = ((crc >> 7) ^ (byte >> i)) & 1
            crc = (crc << 1) & 0xFF
            if feedback:
                crc ^= 0x07
    return crc


def categories():
    """{type: body bytes}, in order, walking from word 0x40 as ecx_siifind does."""
    found, at = {}, 0x40
    while word(at) != 0xFFFF:
        kind, size = word(at), word(at + 1)
        found[kind] = sii.image()[2 * (at + 2):2 * (at + 2 + size)]
        at += 2 + size
    return found


class SiiImageTest(unittest.TestCase):
    def test_image_is_256_bytes(self):
        self.assertEqual(len(sii.image()), 256)

    def test_checksum_of_config_area(self):
        self.assertEqual(sii.image()[14], sii.crc8(sii.image()[:14]))
        for data in (bytes(14), b"\x01\x00" * 7, bytes(range(14))):
            self.assertEqual(sii.crc8(data), bit_serial_crc8(data))

    def test_identity_words(self):
        self.assertEqual(struct.unpack_from("<4I", sii.image(), 2 * 8),
                         (sii.VENDOR, sii.PRODUCT, sii.REVISION, sii.SERIAL))

    def test_no_mailbox(self):
        self.assertEqual([word(n) for n in range(0x18, 0x1D)], [0] * 5)

    def test_word_zero_declares_device_emulation(self):
        self.assertEqual(word(0), 0x0100)

    def test_size_and_version_words(self):
        self.assertEqual((word(0x3E), word(0x3F)), (1, 1))

    def test_category_walk(self):
        self.assertEqual(list(categories()), [10, 30, 40, 41, 50, 51])

    def test_name_string(self):
        body = categories()[10]
        self.assertEqual(body[0], 1)
        self.assertEqual(body[2:2 + body[1]].decode(), sii.NAME)

    def test_fmmu_functions(self):
        self.assertEqual(list(categories()[40][:3]), [1, 2, 0xFF])

    def test_sm_layout(self):
        body = categories()[41]
        self.assertEqual(len(body), 32)
        self.assertEqual(body[:16], bytes(16))
        self.assertEqual(struct.unpack_from("<HHBBBB", body, 16), (sii.OUT_ADDR, sii.PD_BYTES, 0x64, 0, 1, 3))
        self.assertEqual(struct.unpack_from("<HHBBBB", body, 24), (sii.IN_ADDR, sii.PD_BYTES, 0x20, 0, 1, 4))

    def test_pdo_layout(self):
        """Parsed as ecx_siiPDO does: index, entries, SM, then bit length at +5 of each entry."""
        for kind, sm in ((50, 3), (51, 2)):
            body = categories()[kind]
            entries, syncm = body[2], body[3]
            self.assertEqual((entries, syncm), (1, sm))
            self.assertEqual(sum(body[8 + 8 * e + 5] for e in range(entries)), 8 * sii.PD_BYTES)

    def test_read32_past_the_table(self):
        self.assertEqual(sii.read32(8), sii.image()[16:20])
        self.assertEqual(sii.read32(127)[2:], b"\xff\xff")
        self.assertEqual(sii.read32(128), b"\xff" * 4)
        self.assertEqual(sii.read32(0x10008), b"\xff" * 4)

    def test_rom_file_matches_image(self):
        with open(ROM_V, newline="") as f:
            self.assertEqual(f.read(), sii.render_rom())


if __name__ == "__main__":
    unittest.main()
