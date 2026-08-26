#!/usr/bin/env python3
"""Simple L1 Substates inspector/writer for a single PCI device.

Usage:
  l1ss-tool.py -d 00:01.0 --status
  l1ss-tool.py -d 00:01.0 --write --level 1.1 --force

This tool only operates on one device and is conservative: writes require
correct auto-detection of the mask and value. It backs up the original
register value to the current directory before writing.
"""
import argparse
import subprocess
import re
import os
import sys
import time

from collections import OrderedDict

# Pad a number to match the length of the largest number in another variable

def pad_number(num, max_num, pad_char='0'):
    """
    Pads num with pad_char so its length matches maxNum's length.
    
    Args:
        num (int): The number to pad.
        maxNum (int): The number whose length is the reference.
        pad_char (str): The character to pad with (default '0').
    
    Returns:
        str: The padded number as a string.
    """
    # Validate inputs
    if not isinstance(num, int) or not isinstance(max_num, int):
        raise ValueError("Both num and maxNum must be integers.")
    if not isinstance(pad_char, str) or len(pad_char) != 1:
        raise ValueError("pad_char must be a single character string.")

    # Determine target length from reference number
    target_length = len(str(max_num))

    # Format with padding
    return str(num).zfill(target_length) if pad_char == '0' else str(num).rjust(target_length, pad_char)

def parse_pci_address(addr):
    addr = addr.strip()
    # support domain:bus:dev.func or bus:dev.func
    if re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]", addr):
        domain, rest = addr.split(':', 1)
        return domain.lower(), rest
    if re.fullmatch(r"[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]", addr):
        return '0000', addr
    return '0000', addr


def run_lspci_vv(dev):
    # Return decoded textual info only (-vv)
    p = subprocess.Popen(["lspci", "-D", "-vv", "-s", dev], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, _ = p.communicate()
    return out.decode()


def run_lspci_raw(dev):
    # Return raw hex dump only (-xxxx)
    p = subprocess.Popen(["lspci", "-s", dev, "-xxxx"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, _ = p.communicate()
    return out.decode()


def find_l1ss_offset(lspci_text, debug=False):
    if debug: 
        print('\n--- Debug: looking for L1 PM Substates line in decoded output ---')
                
    # look for a line like: Capabilities: [1fc v1] L1 PM Substates
    first_match = None
    lines = lspci_text.splitlines()
    last_line_num = len(lines)
    for line_num, line in enumerate(lines, start=1):
        padded_num = pad_number(line_num, last_line_num)
        match = re.search(r"(?:Capabilities:\s*)?\[([0-9a-fA-F]{1,4})(?:\s*v[0-9]+)?\].*L1 PM Substates", line)
        if match:
            if not first_match:
                first_match = match
            if debug:
                print(f'> {padded_num}: {line}')
            else:
                break
        else:
            if debug: 
                print(f'  {padded_num}: {line}')
    if debug: 
        print('--- end debug ---\n')
    if first_match:
        return int(first_match.group(1), 16)
    return None


def parse_lspci_raw_to_bytes(raw_txt):
    """Parse `lspci -xxxx` raw hex dump into a bytearray indexed by config offset."""
    mem = {}
    maxoff = 0
    for line in raw_txt.splitlines():
        m = re.match(r"^\s*([0-9a-fA-F]{1,3}):\s*((?:[0-9a-fA-F]{2}\s+)+)", line)
        if not m:
            continue
        base = int(m.group(1), 16)
        parts = m.group(2).strip().split()
        for i, b in enumerate(parts):
            try:
                mem[base + i] = int(b, 16)
            except Exception:
                mem[base + i] = 0
        maxoff = max(maxoff, base + len(parts))
    if not mem:
        return None
    ba = bytearray(maxoff)
    for i in range(maxoff):
        ba[i] = mem.get(i, 0)
    return ba


def read_regs_from_raw(raw_txt, base):
    """Return a dictionary from reg name to int value from raw dump for given capability base."""
    ba = parse_lspci_raw_to_bytes(raw_txt)
    if not ba:
        return None
    cap_off = base + 0x04
    ctl1_off = base + 0x08
    ctl2_off = base + 0x0c
    if len(ba) < ctl2_off + 4:
        return None
    cap = int.from_bytes(ba[cap_off:cap_off+4], 'little')
    ctl1 = int.from_bytes(ba[ctl1_off:ctl1_off+4], 'little')
    ctl2 = int.from_bytes(ba[ctl2_off:ctl2_off+4], 'little')
    return {'L1SUBCAP': cap, 'L1SUBCTL1': ctl1, 'L1SUBCTL2': ctl2}



def setpci_read(dev, offset, size='L'):
    # size: B W L
    out = subprocess.check_output(["setpci", "-s", dev, f"{hex(offset)}.{size}"]).decode().strip()
    if out.startswith('0x'):
        out = out[2:]
    return int(out, 16)


def setpci_write(dev, offset, size, value):
    subprocess.check_call(["setpci", "-s", dev, f"{hex(offset)}.{size}={hex(value)}"])


def pretty_print_l1ss_lspci(lspci_text, title='L1 Substates info from lspci -vv'):
    # Print textual lines from lspci about the L1Sub fields if present
    print(title)
    lines = lspci_text.splitlines()
    last_line_num = len(lines)
    for line_num, line in enumerate(lines, start=1):
        if 'L1Sub' in line or 'L1 PM Substates' in line:
            padded_num = pad_number(line_num, last_line_num)
            print(f"  {padded_num}: {line.strip()}")


CAP_AND_CTL_COMMON_NAMES = OrderedDict([
    ('pci_pm_l1_2', 'PCI-PM L1.2'),
    ('pci_pm_l1_1', 'PCI-PM L1.1'),
    ('aspm_l1_2', 'ASPM L1.2'),
    ('aspm_l1_1', 'ASPM L1.1'),
])

CAP_AND_CTL_STATE_DESCS = {
    (None, None): None,
    (None, False): 'Disabled',
    (None, True): 'Enabled',
    (False, False): None,
    (False, True): 'Forced enabled (unsupported)',
    (True, False): 'Disabled',
    (True, True): 'Enabled'
}

def parse_l1subcap(reg_val):
    return {
        'raw': reg_val,
        'pci_pm_l1_2': bool(reg_val & (1 << 0)),
        'pci_pm_l1_1': bool(reg_val & (1 << 1)),
        'aspm_l1_2': bool(reg_val & (1 << 2)),
        'aspm_l1_1': bool(reg_val & (1 << 3)),
        'l1_pm_substates': bool(reg_val & (1 << 4)),
    }


def parse_l1subctl1(reg_val):
    return {
        'raw': reg_val,
        'pci_pm_l1_2': bool(reg_val & (1 << 0)),
        'pci_pm_l1_1': bool(reg_val & (1 << 1)),
        'aspm_l1_2': bool(reg_val & (1 << 2)),
        'aspm_l1_1': bool(reg_val & (1 << 3)),
    }

def parse_l1subctl2(reg_val):
    return {
        "raw": reg_val,

        # bits 0-7
        "t_power_on_value":
            reg_val & 0xff,

        # bits 8-9
        "t_power_on_scale":
            (reg_val >> 8) & 0x3,
    }

def pretty_print_l1subcap(cap_val, offset, indent=''):
    cap_info = parse_l1subcap(cap_val)
    print(f"{indent}[0x{offset:x}] L1SUBCAP=0x{cap_val:08x}")
    for key, name in CAP_AND_CTL_COMMON_NAMES.items():
        if cap_info[key]:
            print(f"{indent}  {name}: Supported")
    if cap_info['l1_pm_substates']:
        print(f"{indent}  L1 PM Substates: Supported")

def pretty_print_l1subctl1(ctl1_val, offset, indent='', cap_val=None):
    ctl1_info = parse_l1subctl1(ctl1_val)
    cap_info = parse_l1subcap(cap_val) if cap_val is not None else None
    print(f"{indent}[0x{offset:x}] L1SUBCTL1=0x{ctl1_val:08x}")
    for key in CAP_AND_CTL_COMMON_NAMES.keys():
        state = CAP_AND_CTL_STATE_DESCS[(cap_info and cap_info[key], ctl1_info[key])]
        name = CAP_AND_CTL_COMMON_NAMES[key] 
        if state:
            print(f"{indent}  {name}: {state}")

def decode_t_power_on(value, scale):
    scale_us = { 0: 2, 1: 10, 2: 100 }
    if scale not in scale_us:
        return None
    return value * scale_us[scale]

def pretty_print_l1subctl2(ctl2_val, offset, indent=''):
    ctl2_info = parse_l1subctl2(ctl2_val)
    print(f"{indent}[0x{offset:x}] L1SUBCTL2=0x{ctl2_info['raw']:08x}")
    power_val = ctl2_info['t_power_on_value']
    power_scale = ctl2_info['t_power_on_scale']
    power_time = decode_t_power_on(power_val, power_scale) or '(unknown)'
    print(f"{indent}  Power On Value: {power_val}")
    print(f"{indent}  Power On Scale: {power_scale}")
    print(f"{indent}  (Power On Time) {power_time} us")

def read_regs_from_setpci(dev, offset):
    """Read L1SUBCAP, L1SUBCTL1, and L1SUBCTL2 registers using setpci."""
    cap_off = offset + 0x04
    ctl1_off = offset + 0x08
    ctl2_off = offset + 0x0c
    cap = setpci_read(dev, cap_off, 'L')
    ctl1 = setpci_read(dev, ctl1_off, 'L')
    ctl2 = setpci_read(dev, ctl2_off, 'L')
    return {'L1SUBCAP': cap, 'L1SUBCTL1': ctl1, 'L1SUBCTL2': ctl2}

def pretty_print_l1ss_raw(offset, regs):
    """Print L1SUB registers nicely formatted."""
    print(f"L1 Substates from registers: [0x{offset:x}]")
    cap_off = offset + 0x04
    ctl1_off = offset + 0x08
    ctl2_off = offset + 0x0c

    if regs:
        cap_val = regs['L1SUBCAP']
        ctl1_val = regs['L1SUBCTL1']
        ctl2_val = regs['L1SUBCTL2']
        pretty_print_l1subcap(cap_val, cap_off, '  ')
        pretty_print_l1subctl1(ctl1_val, ctl1_off, '  ', cap_val)
        pretty_print_l1subctl2(ctl2_val, ctl2_off, '  ')
    else:
        print(f"  L1SUBCAP  @ 0x{cap_off:x}: (unknown)")
        print(f"  L1SUBCTL1 @ 0x{ctl1_off:x}: (unknown)")
        print(f"  L1SUBCTL2 @ 0x{ctl2_off:x}: (unknown)")


def backup_register(dev, ctl_off):
    orig = setpci_read(dev, ctl_off, 'L')
    backup = os.path.join(os.getcwd(), f'l1ss-backup-{dev.replace(":","_")}-0x{ctl_off:x}.orig')
    if os.path.exists(backup):
        print(f"Backup file already exists: {backup}")
        print(f"  Original value in backup: {open(backup).read().strip()}")
        print(f"  Current register value: 0x{orig:08x}")
    else:
        with open(backup, 'w') as f:
            f.write(hex(orig) + '\n')
        print(f'Backup written to: {backup}')
    return orig


def compute_new_value(orig, mask, val):
    return (orig & (~mask & 0xffffffff)) | (val & mask)


def perform_write_and_verify(dev, ctl_off, new):
    setpci_write(dev, ctl_off, 'L', new)
    rb = setpci_read(dev, ctl_off, 'L')
    return rb == new, rb


def restore_register(dev, ctl_off, orig):
    setpci_write(dev, ctl_off, 'L', orig)
    return setpci_read(dev, ctl_off, 'L')


def default_l1ss_write_for_level(level):
    """Return the canonical low-nibble mask/value for a requested L1SS level.

    For L1SubCtl1, the control bits are defined as:
      bit 0 = PCI-PM_L1.2
      bit 1 = PCI-PM_L1.1
      bit 2 = ASPM_L1.2
      bit 3 = ASPM_L1.1
    """
    if level == 'both':
        return 0x0F, 0x0F
    elif level == '1.1':
        return 0x0F, 0x08
    elif level == '1.2':
        return 0x0F, 0x04
    else:
        raise ValueError(f'Unknown level: {level}')


def detect_bits_from_lspci(lspci_text):
    """Decode the L1SubCtl1 state fields from lspci text.

    The L1 PM Substates control register uses the low four bits of L1SubCtl1:
      bit 0 = PCI-PM_L1.2
      bit 1 = PCI-PM_L1.1
      bit 2 = ASPM_L1.2
      bit 3 = ASPM_L1.1

    This function does not guess bit positions from the raw register value; it
    maps each named flag in the lspci output to the canonical register bit.
    Returns (mask, value, details_dict) or (None, None, details) if inconclusive.
    """
    ctl_line = None
    for line in lspci_text.splitlines():
        if 'L1SubCtl1:' in line or 'L1SubCtl:' in line:
            ctl_line = line.strip()
            break
    details = {'line': ctl_line}
    if not ctl_line:
        return None, None, details

    tokens = ['PCI-PM_L1.2', 'PCI-PM_L1.1', 'ASPM_L1.2', 'ASPM_L1.1']
    bit_map = {
        'PCI-PM_L1.2': 0,
        'PCI-PM_L1.1': 1,
        'ASPM_L1.2': 2,
        'ASPM_L1.1': 3,
    }

    flags = {}
    for token in tokens:
        m = re.search(re.escape(token) + r'([+-])', ctl_line)
        if m:
            state = 1 if m.group(1) == '+' else 0
            flags[token] = {'bit': bit_map[token], 'state': state}

    details['flags'] = flags

    mask = 0
    value = 0
    for token, info in flags.items():
        bit = info['bit']
        mask |= (1 << bit)
        if info['state']:
            value |= (1 << bit)

    if not flags:
        return None, None, details
    return mask, value, details


def main():
    parser = argparse.ArgumentParser(description='L1 Substates tool (single device)')
    parser.add_argument('-d', '--device', required=True, help="PCI device (eg 0000:01:00.0 or 01:00.0)")
    parser.add_argument('--write', action='store_true', help='Write L1SS control register (requires offset/mask/value and --force)')
    parser.add_argument('--offset', help='Hex offset of capability (e.g. 0x1fc)')
    parser.add_argument('--force', action='store_true', help='Actually perform the write')
    parser.add_argument('--trial', action='store_true', help='Temporarily set bits then restore after --wait seconds (allows trying without permanent change)')
    parser.add_argument('--wait', type=int, default=5, help='Seconds to wait in --trial mode before restoring (default 5)')
    parser.add_argument('--level', choices=('1.1','1.2','both'), default='1.1', help='Which L1 substate(s) to try in --trial mode')
    parser.add_argument('--restore', help='Restore a backup file created by this tool (provide backup file path)')
    parser.add_argument('--debug', action='store_true', help='Print debug info (show lspci output used)')
    parser.add_argument('--lspci-file', help='Use a saved lspci -vv output file instead of running lspci')
    parser.add_argument('--lspci-raw-file', help='Use a saved lspci -xxxx raw output file instead of running lspci -xxxx')
    args = parser.parse_args()

    domain, devaddr = parse_pci_address(args.device)
    busid = f"{domain}:{devaddr}" if domain != '0000' else devaddr

    # Allow using saved lspci outputs for reproducible debugging/testing
    offline_mode = False
    decoded_txt = None
    raw_txt = None
    if args.lspci_file:
        offline_mode = True
        try:
            with open(args.lspci_file, 'r', encoding='utf-8') as f:
                decoded_txt = f.read()
        except Exception as e:
            print(f'Failed to read lspci file: {e}')
            sys.exit(2)
    if args.lspci_raw_file:
        offline_mode = True
        if not args.lspci_file:
            print('If specifying --lspci-raw-file, you must also specify --lspci-file.')
            sys.exit(1)
        try:
            with open(args.lspci_raw_file, 'r', encoding='utf-8') as f:
                raw_txt = f.read()
        except Exception as e:
            print(f'Failed to read lspci raw file: {e}')
            sys.exit(2)
    if not offline_mode:
        decoded_txt = run_lspci_vv(busid)
        raw_txt = run_lspci_raw(busid)

    if offline_mode:
        print('OFFLINE MODE: using saved lspci output; write/trial/restore disabled')

    offset = None
    if args.offset:
        try:
            offset = int(args.offset, 16)
        except Exception:
            print('Invalid --offset')
            sys.exit(1)
    else:
        offset = find_l1ss_offset(decoded_txt, debug=args.debug)

    if offset is None:
        print('Could not find L1 PM Substates capability in `lspci -vv` output.')
        # If debug requested, show any nearby lines that mention L1, ASPM or related keywords
        if args.debug:
            print('Use --offset to specify the capability manually.')
            print('\n--- Debug: lines mentioning L1/ASPM ---')
            for i, line in enumerate(decoded_txt.splitlines(), start=1):
                if 'L1' in line or 'ASPM' in line or 'L1Sub' in line or 'L1 PM Substates' in line:
                    padded_num = pad_number(i, len(decoded_txt.splitlines()))
                    print(f'> {padded_num}: {line}')
            print('--- end debug ---\n')
        sys.exit(0)

    # decoded textual detection (needs ctl1 read if possible)
    ctl1_off = offset + 0x08
    ctl1_val_orig = None
    
    regs = None
    if raw_txt:
        # When offline,extract register values from the raw file (if provided)
        regs = read_regs_from_raw(raw_txt, offset)
        if regs and 'L1SUBCTL1' in regs:
            ctl1_val_orig = regs['L1SUBCTL1']
    if not offline_mode:
        regs = read_regs_from_setpci(busid, offset)
        
    print("")
    pretty_print_l1ss_lspci(decoded_txt)
    print("")
    pretty_print_l1ss_raw(offset, regs=regs)
    print("")

    ctl1_off = offset + 0x08
    if not offline_mode:
        ctl1_val_orig = setpci_read(busid, ctl1_off, 'L')
    try:
        detected = detect_bits_from_lspci(decoded_txt)
        if detected and detected[0] is not None:
            mask, val, _ = detected
            if val == 0 and args.level in ('1.1', '1.2', 'both'):
                mask, val = default_l1ss_write_for_level(args.level)
        else:
            mask, val = default_l1ss_write_for_level(args.level)
    except ValueError as exc:
        raise
    (old_mask, old_val, info) = detect_bits_from_lspci(decoded_txt)
    if old_mask is not None and old_val is not None:
        print('Auto-detected mask/value from decoded lspci:')
        print(f"  {info['line']}")
        if ctl1_val_orig is None:
            print(f"  ctl1=(unknown)")
        else:
            print(f"  ctl1=0x{ctl1_val_orig:08x}")
        print(f"  mask=0x{mask:08x}"); 
        print(f'   val=0x{val:08x}')
        for (k,v) in info['flags'].items():
            print(f"  ctl1[{v['bit']}] = {v['state']} ({k})")

    if mask == 0:
        print('Auto-detection failed from decoded lspci; cannot write')
        sys.exit(1)
    mask, val = default_l1ss_write_for_level(args.level)

    if args.restore:
        if offline_mode:
            print('Offline mode: cannot restore from backup file when using --lspci-file. Run on the target machine to perform restore.')
            return
        backup_file = args.restore
        if not os.path.exists(backup_file):
            print(f"Backup file not found: {backup_file}")
            return
        # read original value from backup
        with open(backup_file, 'r') as f:
            first = f.readline().strip()
        if not first:
            print(f"Backup file {backup_file} is empty")
            return
        try:
            orig = int(first, 16) if first.startswith('0x') else int(first, 16)
        except Exception:
            print(f"Cannot parse hex value from backup file: {first}")
            return
        # try to infer ctl offset from filename, otherwise use discovered offset
        m = re.search(r'0x([0-9a-fA-F]+)', os.path.basename(backup_file))
        if m:
            try:
                ctl_off = int(m.group(1), 16)
            except Exception:
                ctl_off = None
        else:
            if offset is None:
                print("Cannot determine control offset; provide a backup filename containing 0x<offset> or rerun with --offset")
                return
            ctl_off = offset + 0x08
        print(f"Restoring {hex(orig)} to {busid} @ 0x{ctl_off:x} from {backup_file}")
        written_val = restore_register(busid, ctl_off, orig)
        print(f"Readback after restore: 0x{written_val:08x}")
        return

    if args.trial or args.write:
        ctl1_off = offset + 0x08
        orig = ctl1_val_orig if offline_mode else backup_register(busid, ctl1_off)
        try:
            new_ctl1_val = compute_new_value(orig, mask, val)
        except Exception:
            raise
        print(f'Original L1SUBCTL1 @ 0x{ctl1_off:x}: 0x{orig:08x}')
        print(f'Planned new value: 0x{new_ctl1_val:08x} (mask 0x{mask:x}, value 0x{val:x})')
        pretty_print_l1subctl1(new_ctl1_val, ctl1_off, ' ', regs['L1SUBCAP'])

        action_desc = 'trial write' if args.trial else 'write'
        if offline_mode:
            print(f'Offline mode: cannot perform {action_desc} when using --lspci-file. Run on the target machine instead.')
            return
        if not args.force:
            print(f'Dry-run: use --force to actually perform the {action_desc}')
            return

        print(f'Performing {action_desc}...')
        ok, written_val = perform_write_and_verify(busid, ctl1_off, new_ctl1_val)
        pretty_print_l1ss_lspci(run_lspci_vv(busid), 'after write')

        if not ok:
            print(f'Write mismatch (read 0x{written_val:08x}), restoring original...')
            restore_register(busid, ctl1_off, orig)
            print('Restored.')
            pretty_print_l1ss_lspci(run_lspci_vv(busid), 'after restore')
            return

        print(f'Write verified: 0x{written_val:08x}')

        # For trial mode, wait then restore the original value regardless of success
        if args.trial:
            try:
                print(f'Waiting {args.wait} seconds before restoring...')
                time.sleep(args.wait)
            except Exception as e:
                print(f'Error during trial wait: {e}')
            finally:
                print('Restoring original value...')
                restored_val = restore_register(busid, ctl1_off, orig)
                print(f'Readback after restore: 0x{restored_val:08x}')
                pretty_print_l1ss_lspci(run_lspci_vv(busid), 'after restore')

if __name__ == '__main__':
    main()
