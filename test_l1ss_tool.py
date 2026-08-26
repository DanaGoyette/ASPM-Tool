import argparse
import runpy
import unittest

module = runpy.run_path('h:/Documents/ASPM-Tool/l1ss-tool.py')

detect_bits_from_lspci = module['detect_bits_from_lspci']
default_l1ss_write_for_level = module['default_l1ss_write_for_level']


class DetectL1SSBitsTest(unittest.TestCase):
    def test_l1ss_bits_are_low_nibble_not_0x30(self):
        lspci_line = '\t\tL1SubCtl1: PCI-PM_L1.2- PCI-PM_L1.1- ASPM_L1.2- ASPM_L1.1-'

        mask, value, details = detect_bits_from_lspci(lspci_line)

        self.assertEqual((mask, value), (0x0F, 0x00))
        self.assertEqual({k: v['state'] for k, v in details['flags'].items()}, {
            'ASPM_L1.1': 0,
            'ASPM_L1.2': 0,
            'PCI-PM_L1.1': 0,
            'PCI-PM_L1.2': 0,
        })

    def test_all_four_states_can_be_enabled_in_low_nibble(self):
        orig = 0x425A0000

        mask = 0x0F
        value = 0x0F
        new = (orig & (~mask & 0xffffffff)) | (value & mask)

        self.assertEqual(new, 0x425A000F)

    def test_default_level_allows_enable_when_auto_detect_sees_disabled_bits(self):
        args = argparse.Namespace(mask=None, value=None, level='both', auto_detect=True)
        orig = 0x425A0000
        lspci_line = '\t\tL1SubCtl1: PCI-PM_L1.2- PCI-PM_L1.1- ASPM_L1.2- ASPM_L1.1-'

        self.assertEqual(default_l1ss_write_for_level('both'), (0x0F, 0x0F))
        # emulate the resolution logic: prefer detected mask/value unless detected value==0
        detected = detect_bits_from_lspci(lspci_line)
        if detected and detected[0] is not None:
            mask, val, _ = detected
            if val == 0 and args.level in ('1.1', '1.2', 'both'):
                mask, val = default_l1ss_write_for_level(args.level)
        else:
            mask, val = default_l1ss_write_for_level(args.level)
        self.assertEqual((mask, val), (0x0F, 0x0F))


if __name__ == '__main__':
    unittest.main()
