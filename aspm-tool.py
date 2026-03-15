#!/usr/bin/env python3

import subprocess
import argparse
import re
import logging as log
from enum import Enum
from dataclasses import dataclass


class ASPM(Enum):
	ASPM_DISABLED = 0b00
	L0s = 0b01
	L1 = 0b10
	L0s_AND_L1 = 0b11


@dataclass
class Device:
	addr: str
	path: str
	name: str
	cap: ASPM = ASPM.ASPM_DISABLED
	mode: ASPM = ASPM.ASPM_DISABLED


help_des = '''View and set PCI device's ASPM settings.

Will print out all device details by default.'''
help_ex = '''examples:
  List all devices:
  %(prog)s

  Set all devices according to their claimed capability:
  %(prog)s -s auto

  Disable ASPM on device with address 00:08.1:
  %(prog)s -d 00:08.1 -s disable

'''


def get_device_name(addr):
	p = subprocess.Popen([
		"lspci",
		"-s",
		addr,
	], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
	return p.communicate()[0].splitlines()[0].decode()


def read_all_bytes(device):
	all_bytes = bytearray()
	device_name = get_device_name(device)
	p = subprocess.Popen([
		"lspci",
		"-s",
		device,
		"-xxx"
	], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
	ret = p.communicate()
	ret = ret[0].decode()
	for line in ret.splitlines():
		if not device_name in line and ": " in line:
			all_bytes.extend(bytearray.fromhex(line.split(": ")[1]))
	if len(all_bytes) < 256:
		print(f"Expected 256 bytes, only got {len(all_bytes)} bytes!")
		print("Are you running this as root?")
		exit()
	return all_bytes


def find_byte_to_patch(bytes, pos):
	log.info(f"{hex(pos)} points to {hex(bytes[pos])}")
	pos = bytes[pos]
	log.info(f"Value at {hex(pos)} is {hex(bytes[pos])}")
	if bytes[pos] != 0x10:
		log.info("Value is not 0x10!")
		log.info("Reading the next byte...")
		pos += 0x1
		return find_byte_to_patch(bytes, pos)
	else:
		log.info(f"Found the byte at: {hex(pos)}")
		log.info("Adding 0x10 to the register...")
		pos += 0x10
		log.info(f"Final register reads: {hex(bytes[pos])}")
		return pos


def patch_byte(device, position, value):
	subprocess.Popen([
		"setpci",
		"-s",
		device,
		f"{hex(position)}.B={hex(value)}"
	]).communicate()


def patch_device(addr, mode):
	print(f"Setting device {addr} to {mode.name}...")
	endpoint_bytes = read_all_bytes(addr)
	byte_position_to_patch = find_byte_to_patch(endpoint_bytes, 0x34)

	log.info(f"Position of byte to patch: {hex(byte_position_to_patch)}")
	log.info(f"Byte is set to {hex(endpoint_bytes[byte_position_to_patch])}")
	log.info(f"-> {ASPM(int(endpoint_bytes[byte_position_to_patch]) & 0b11).name}")

	if int(endpoint_bytes[byte_position_to_patch]) & 0b11 != mode.value:
		log.info("Value doesn't match the one we want, setting it!")
		
		patched_byte = int(endpoint_bytes[byte_position_to_patch])
		patched_byte = patched_byte >> 2
		patched_byte = patched_byte << 2
		patched_byte = patched_byte | mode.value

		patch_byte(addr, byte_position_to_patch, patched_byte)
		new_bytes = read_all_bytes(addr)
		log.info(f"Byte is now set to {hex(new_bytes[byte_position_to_patch])}")
		log.info(f"-> {ASPM(int(new_bytes[byte_position_to_patch]) & 0b11).name}")
	else:
		print(f"Device {addr} is already set to {mode.name}!")


def set_device(device, mode):
	if "/" in device.path:
		patch_device(device.path.split('/')[-2], mode)
		patch_device(device.path.split('/')[-1], mode)
	else:
		patch_device(device.addr, mode)


def string_to_aspm(input):
	if "L0s L1" in input:
		return ASPM.L0s_AND_L1
	if "L0s" in input:
		return ASPM.L0s
	if "L1" in input:
		return ASPM.L1
	return ASPM.ASPM_DISABLED


def read_capability(device):
	device_addr = device.addr
	if '/' in device.addr:
		device_addr = device.addr.split('/')[1]
	p = subprocess.Popen([
		"lspci",
		"-vv",
		"-s",
		device_addr,
	], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
	output, errs = p.communicate()
	output_text = output.decode()
	if "LnkCap:" not in output_text:
		# Device has no link to configure, remove from list
		return None
	else:
		cap_pattern = r', ASPM.*?,'
		cap_match = re.search(cap_pattern, output_text)
		state_pattern = r'LnkCtl:\tASPM.*;'
		state_match = re.search(state_pattern, output_text)
		if state_match:
			device.mode = string_to_aspm(state_match.group())
		if cap_match:
			device.cap = string_to_aspm(cap_match.group())
		return device


def identify_devices():
	dev_list = []
	p = subprocess.Popen([
			"lspci",
			"-nn",
			"-PP",
		], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
	
	output, errs = p.communicate()
	for line in output.decode().splitlines():
		parts = line.split(' ')
		path = parts[0]
		addr = path.split('/')[-1]
		new_device = Device(addr=addr, path=path, name=' '.join(parts[1:]))
		new_device = read_capability(new_device)
		dev_list.append(new_device)
	return filter(None, dev_list)


def main():
	parser = argparse.ArgumentParser(
		description=help_des,
		epilog=help_ex,
		formatter_class=argparse.RawTextHelpFormatter)
	parser.add_argument(
		"-d",
		"--device",
		help="Specify device address. Can be just the endpoint or root/endpoint. Eg '00:02.0' or '00:08.1/04:00.5'")
	parser.add_argument(
		"-s",
		"--set",
		choices=('auto', 'disable', 'L0s', 'L1', 'L0s_AND_L1'),
		help="Set ASPM mode, auto will set according to device's reported capability. Combine with -d to specify a particular device to apply the setting to.")
	parser.add_argument(
		"-v",
		"--verbose",
		action="store_true",
		help="Verbose output when setting.")
	args = parser.parse_args()
	
	if args.verbose:
		log.basicConfig(format="%(message)s", level=log.DEBUG)
	else:
		log.basicConfig(format="%(message)s")
	
	# Get the details of all devices on the system
	dev_list = identify_devices()
	
	# Device specified, reduce list to single device matching that address
	if args.device:
		selected_device = next((dev for dev in dev_list if dev.addr == args.device or dev.addr[-7:] == args.device), None)
		if selected_device:
			dev_list = [selected_device]
		else:
			print(f"No device found with address: {args.device}")
			exit()
	
	# Command to set the ASPM bits
	if args.set:
		for device in dev_list:
			if args.set == 'auto':
				set_device(device, device.cap)
			if args.set == 'disable':
				set_device(device, ASPM.ASPM_DISABLED)
			if args.set == 'L0s':
				set_device(device, ASPM.L0s)
			if args.set == 'L1':
				set_device(device, ASPM.L1)
			if args.set == 'L0s_AND_L1':
				set_device(device, ASPM.L0s_AND_L1)
	else:
		# Just print
		for device in dev_list:
			print(f"{device.addr} {device.name}")
			print(f"\tPath: {device.path}")
			print(f"\tCapable: {device.cap.name}")
			print(f"\tCurrent: {device.mode.name}")
			print("")


if __name__ == "__main__":
	main()
