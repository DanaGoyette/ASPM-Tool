# ASPM-Tool
`aspm-tool.py` is a python script that can read and modify PCI ASPM settings using `lspci` and `setpci`. It uses z8's fantastic `aspm.py` code to set the ASPM settings. It can list devices, their current setting and what setting they claim to support. You can set with `auto`, and it will configure all devices, identifying the corresponding root_complex and configure it to match. You can instead specify PCI devices by their address and mode to force a particular configuration if needed.

#### Warning
This tool performs writes to your PCI device configuration registers based on regex of command line tools. This should be a last resort when configuring such things, your BIOS, OS and device drivers really should be doing this for you. While this tool on it's own worked for me running OmniOS, you may need to also use `ASPMEnabler` and/or change the myriad of things z8 goes through on their [blog post](https://z8.re/blog/aspm).

#### Requirements
Uses only built-in python modules, but does require at least python3.7 and `pciutils` to be installed, namely `lspci` and `setpci`. It is standalone, so doesn't need `aspm.py` or any other files to run.

#### Usage
```
./aspm-tool.py --help
usage: aspm-tool.py [-h] [-d DEVICE] [-s {auto,disable,L0s,L1,L0s_AND_L1}] [-v]

View and set PCI device's ASPM settings.

Will print out all device details by default.

options:
	-h, --help      show this help message and exit
	-d DEVICE, --device DEVICE
									Specify device address. Can be just the endpoint or root/endpoint.
									Eg '00:02.0' or '00:08.1/04:00.5'
	-s {auto,disable,L0s,L1,L0s_AND_L1}, --set {auto,disable,L0s,L1,L0s_AND_L1}
									Set ASPM mode, auto will set according to device's reported capability.
									Combine with -d to specify a particular device to apply the setting to.
	-v, --verbose   Verbose output when setting.

examples:
	List all devices:
	aspm-tool.py

	Set all devices according to their claimed capability:
	aspm-tool.py -s auto

	Disable ASPM on device with address 00:08.1:
	aspm-tool.py -d 00:08.1 -s disable
```

# ASPM

`aspm.py` is a re-implementation of the `enable_aspm.sh` script originally written by Luis R. Rodriguez.

`ASPMEnabler` is an EFI executable that patches the FADT ACPI table, based on S0ixEnabler by James Swineson.

For more information please see this blog post: [https://z8.re/blog/aspm](https://z8.re/blog/aspm)