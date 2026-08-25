#!/usr/bin/env python3
"""Simple L1 Substates inspector/writer for a single PCI device.

Usage:
  l1ss-tool.py -d 00:01.0 --status
  l1ss-tool.py -d 00:01.0 --write --offset 0x1fc --mask 0x30 --value 0x30 --force

This tool only operates on one device and is conservative: writes require
explicit --offset, --mask, --value and --force. It backs up the original
register value to the current directory before writing.
"""
import argparse
import subprocess
import re
import os
import sys
import time

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
    """Return (cap, ctl1, ctl2) ints from raw dump for given capability base."""
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
    return (cap, ctl1, ctl2)


def guess_offset_mask_from_raw(raw_txt):
    """Heuristic: scan the raw config dump for places where the 32-bit word
    at offset+0x08 contains ASPM L1.1/L1.2 bits (0x10/0x20). Return a tuple
    (cap_base_offset, mask, value, details) or (None, None, None, details).
    """
    ba = parse_lspci_raw_to_bytes(raw_txt)
    details = {'candidates': []}
    if not ba:
        return None, None, None, details
    maxoff = len(ba)
    # scan reasonable range (0..maxoff-12)
    for off in range(0, maxoff - 12):
        ctl1_off = off + 0x08
        if ctl1_off + 4 > maxoff:
            continue
        val = int.from_bytes(ba[ctl1_off:ctl1_off + 4], 'little')
        # prefer locations where L1.1 or L1.2 bits appear
        if (val & 0x30) != 0:
            details['candidates'].append({'base': off, 'ctl1': val})
            mask = 0
            value = 0
            if val & 0x10:
                mask |= 0x10
                value |= 0x10
            if val & 0x20:
                mask |= 0x20
                value |= 0x20
            return off, mask, value, details
    return None, None, None, details


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

def pretty_print_l1ss(dev, offset, regs=None, offline_mode=False):
    """Print L1SUB registers. If regs tuple (cap,ctl1,ctl2) is provided, use
    that instead of calling setpci. When offline_mode is True and regs is
    None, do not attempt to call setpci and instead indicate registers are
    unavailable."""
    if offline_mode and regs is None:
        raise ValueError('Offline: no raw file provided; register values are not available')
        
    print(f"L1 PM Substates capability found at 0x{offset:x}")
    ctl1_off = offset + 0x08
    ctl2_off = offset + 0x0c
    cap_off = offset + 0x04
    if regs is not None:
        cap, ctl1, ctl2 = regs
    else:
        cap = setpci_read(dev, cap_off, 'L')
        ctl1 = setpci_read(dev, ctl1_off, 'L')
        ctl2 = setpci_read(dev, ctl2_off, 'L')
    print(f"  L1SUBCAP  @ 0x{cap_off:x}: 0x{cap:08x}")
    print(f"  L1SUBCTL1 @ 0x{ctl1_off:x}: 0x{ctl1:08x}  ({bin(ctl1)})")
    print(f"  L1SUBCTL2 @ 0x{ctl2_off:x}: 0x{ctl2:08x}  ({bin(ctl2)})")
    print("")



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
    if level == '1.1':
        return 0x0F, 0x08
    if level == '1.2':
        return 0x0F, 0x04
    return 0x0F, 0x0F


def detect_bits_from_lspci(lspci_text, orig):
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
            ctl_line = line
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

    wanted = {}
    for token in tokens:
        m = re.search(re.escape(token) + r'([+-])', ctl_line)
        if m:
            wanted[token] = 1 if m.group(1) == '+' else 0
    details['wanted'] = wanted

    mask = 0
    value = 0
    chosen = {}
    for token in tokens:
        if token in wanted:
            bit = bit_map[token]
            chosen[token] = bit
            mask |= (1 << bit)
            if wanted[token]:
                value |= (1 << bit)

    details['chosen'] = chosen
    details['candidates'] = {token: [bit_map[token]] for token in chosen}
    if not chosen:
        return None, None, details
    return mask, value, details


def resolve_l1ss_write_target(args, orig, decoded_txt):
    """Resolve a target mask/value for a write or trial.

    If the user supplied explicit values, respect those. Otherwise prefer the
    requested --level defaults when auto-detect sees all bits disabled; this
    avoids writing a zero-value target when the user clearly intends to enable
    the substate(s).
    """
    if args.mask and args.value:
        try:
            return int(args.mask, 16), int(args.value, 16)
        except Exception:
            raise ValueError('Invalid mask/value')

    if args.auto_detect:
        detected = detect_bits_from_lspci(decoded_txt, orig)
        if detected and detected[0] is not None:
            mask, value, details = detected
            if value == 0 and args.level in ('1.1', '1.2', 'both'):
                return default_l1ss_write_for_level(args.level)
            return mask, value

    return default_l1ss_write_for_level(args.level)


def main():
    parser = argparse.ArgumentParser(description='L1 Substates tool (single device)')
    parser.add_argument('-d', '--device', required=True, help="PCI device (eg 0000:01:00.0 or 01:00.0)")
    parser.add_argument('--status', action='store_true', help='Show L1SS capability and control registers')
    parser.add_argument('--write', action='store_true', help='Write L1SS control register (requires offset/mask/value and --force)')
    parser.add_argument('--offset', help='Hex offset of capability (e.g. 0x1fc)')
    parser.add_argument('--mask', help='Hex mask to apply to the control register (e.g. 0x30)')
    parser.add_argument('--value', help='Hex value to OR (masked) into the control register (e.g. 0x30)')
    parser.add_argument('--force', action='store_true', help='Actually perform the write')
    parser.add_argument('--trial', action='store_true', help='Temporarily set bits then restore after --wait seconds (allows trying without permanent change)')
    parser.add_argument('--wait', type=int, default=5, help='Seconds to wait in --trial mode before restoring (default 5)')
    parser.add_argument('--level', choices=('1.1','1.2','both'), default='both', help='Which L1 substate(s) to try in --trial mode')
    parser.add_argument('--auto-detect', action='store_true', help='Attempt to detect bit positions from lspci text and current register')
    parser.add_argument('--restore', help='Restore a backup file created by this tool (provide backup file path)')
    parser.add_argument('--debug', action='store_true', help='Print debug info (show lspci output used)')
    parser.add_argument('--dry-parse', action='store_true', help='Print detected/guessed capability offsets and masks (no writes)')
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
        print('Could not find L1 PM Substates capability in decoded `lspci -vv`.')
        print('Use --offset to specify manually or run with --status to inspect. Raw guessing is only available with --dry-parse.')
        # If debug requested, show any nearby lines that mention L1 or L1 PM Substates
        return

    if args.dry_parse:
        if offline_mode and not raw_txt:
            print('\nDecoded-detection: offline mode (no raw file); cannot dry-parse')
            sys.exit(1)

        print('Dry-parse: finding and reporting decoded and raw candidates (no changes)')

        off_guess, mask_guess, val_guess, det_raw = guess_offset_mask_from_raw(raw_txt)
        if offset is None:
            # For dry-parse only: try to guess from the raw dump (informational)
            if off_guess is not None:
                print(f'Could not find decoded capability; guessed base at 0x{off_guess:x} from raw dump (dry-parse only)')
                offset = off_guess
            else:
                print('Could not find L1 PM Substates capability (decoded or raw).')
                return

        # raw guesses
        print('\nRaw-guess detection:')
        print(det_raw)
        if off_guess is not None:
            print(f"Guessed base: 0x{off_guess:x}, mask=0x{mask_guess:x}, value=0x{val_guess:x}")
        else:
            print('No raw candidates found')
        
        # decoded textual detection (needs ctl1 read if possible)
        ctl1_off = offset + 0x08
        
        if offline_mode:
            # read from raw dump
            regs = None
            try:
                regs = parse_lspci_raw_to_bytes(raw_txt)
            except Exception as e:
                regs = None
                print('\nDecoded-detection: failed to read bytes from text:', e)
            if regs is not None and len(regs) > ctl1_off + 3:
                orig_val = int.from_bytes(regs[ctl1_off:ctl1_off+4], 'little')
                det_dec = detect_bits_from_lspci(decoded_txt, orig_val)
                print('\nDecoded-detection:')
                print(det_dec)
        else:
            try:
                orig_val = setpci_read(busid, ctl1_off, 'L')
                det_dec = detect_bits_from_lspci(decoded_txt, orig_val)
                print('\nDecoded-detection:')
                print(det_dec)
            except Exception as e:
                print('\nDecoded-detection: failed to read ctl1:', e)
        return

    # If the user supplied an lspci file, run in offline mode: disable writes/trials/restores
    if offline_mode:
        if args.write or args.trial or args.restore:
            print('Offline mode: write/trial/restore operations are disabled when using --lspci-file. Run on the target machine to perform writes.')
            return
        if (args.status or args.dry_parse) and not raw_txt:
            print('Offline mode: status or dry-parse requires a raw lspci file (--lspci-raw-file).')
            sys.exit(1)

    if not (args.write or args.trial or args.restore or args.status or args.dry_parse):
        args.status = True

    if args.status:
        # When offline, prefer to extract register values from the raw file (if provided)
        regs = None
        if offline_mode:
            if raw_txt:
                regs = read_regs_from_raw(raw_txt, offset)
            else:
                regs = None
        pretty_print_l1ss(busid, offset, regs=regs, offline_mode=offline_mode)
        pretty_print_l1ss_lspci(decoded_txt)

    if args.restore:
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

    if args.write:
        # Determine mask/value: prefer explicit args, otherwise allow auto-detect
        if not args.mask or not args.value:
            if not args.auto_detect:
                print('Writes require --mask and --value to be specified.')
                return
            ctl1_off = offset + 0x08
            orig_tmp = setpci_read(busid, ctl1_off, 'L')
            try:
                mask, val = resolve_l1ss_write_target(args, orig_tmp, decoded_txt)
            except ValueError as exc:
                print(str(exc))
                return
            detected = detect_bits_from_lspci(decoded_txt, orig_tmp)
            if detected and detected[0] is not None:
                print('Auto-detected mask/value from decoded lspci:')
                print(detected[2])
            if mask == 0 and val == 0:
                print('Auto-detection failed from decoded lspci; please supply --mask and --value')
                return
        else:
            try:
                mask = int(args.mask, 16)
                val = int(args.value, 16)
            except Exception:
                print('Invalid mask/value')
                return

        ctl1_off = offset + 0x08
        orig = backup_register(busid, ctl1_off)
        try:
            new = compute_new_value(orig, mask, val)
        except Exception:
            print('Error computing new value')
            return
        print(f'Original L1SUBCTL1 @ 0x{ctl1_off:x}: 0x{orig:08x}')
        print(f'Planned new value: 0x{new:08x} (mask 0x{mask:x}, value 0x{val:x})')
        if not args.force:
            print('Dry-run: use --force to actually perform the write')
            return
        print('Performing write...')
        ok, written_val = perform_write_and_verify(busid, ctl1_off, new)
        pretty_print_l1ss_lspci(run_lspci_vv(busid), 'after write')
        if ok:
            print(f'Write verified: 0x{written_val:08x}')
        else:
            print(f'Write mismatch (read 0x{written_val:08x}), restoring original...')
            restore_register(busid, ctl1_off, orig)
            print('Restored.')
            pretty_print_l1ss_lspci(run_lspci_vv(busid), 'after restore')
                    
        return

    if args.trial:
        # Trial mode: compute default mask/value for level if not provided, write, wait, restore
        ctl1_off = offset + 0x08
        orig = backup_register(busid, ctl1_off)
        # Determine mask/value
        if args.mask and args.value:
            try:
                mask = int(args.mask, 16)
                val = int(args.value, 16)
            except Exception:
                print('Invalid mask/value')
                return
        else:
            if args.auto_detect:
                detected = detect_bits_from_lspci(decoded_txt, orig)
                if detected and detected[0] is not None:
                    mask, value, details = detected
                    if value == 0 and args.level in ('1.1', '1.2', 'both'):
                        mask, value = default_l1ss_write_for_level(args.level)
                        details['note'] = 'No current L1SS bits enabled; using --level default'
                    print('Auto-detected mask/value from decoded lspci:')
                    print(details)
                    val = value
                else:
                    print('Auto-detection failed from decoded lspci; falling back to conservative defaults')
                    mask = None
            else:
                mask = None
            if mask is None:
                mask, val = default_l1ss_write_for_level(args.level)
        try:
            new = compute_new_value(orig, mask, val)
        except Exception:
            print('Error computing new value')
            return
        print(f'Original L1SUBCTL1 @ 0x{ctl1_off:x}: 0x{orig:08x}')
        print(f'Trial new value: 0x{new:08x} (mask 0x{mask:x}, value 0x{val:x})')
        if not args.force:
            print('Dry-run: use --force to actually perform the trial write')
            return
        try:
            print('Performing trial write...')
            ok, written_val = perform_write_and_verify(busid, ctl1_off, new)
            print(f'Readback after write: 0x{written_val:08x}')
            pretty_print_l1ss_lspci(run_lspci_vv(busid), 'during trial')
            print(f'Waiting {args.wait} seconds before restoring...')
            time.sleep(args.wait)
        except Exception as e:
            print(f'Error during trial write: {e}')
            raise
        finally:
            print('Restoring original value...')
            restored_val = restore_register(busid, ctl1_off, orig)
            print(f'Readback after restore: 0x{restored_val:08x}')
            pretty_print_l1ss_lspci(run_lspci_vv(busid), 'after restore')


if __name__ == '__main__':
    main()
