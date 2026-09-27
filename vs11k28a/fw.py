# /// script
# requires-python = ">=3.11"
# dependencies = ["hidapi>=0.15.0"]
# ///

import hashlib
import importlib.util
import sys
import time
from pathlib import Path
import hid as hidapi

VID = 0x320F
PID = 0x5064
BOOT_VID = 0x0C45
BOOT_PID = 0x7687

BASE_FIRMWARE = Path(__file__).parent / "firmware_update_payload.bin"
BASE_ADDRESS = 0x2800
PATCH_DIR = Path(__file__).parent / "patches"

REPORT_SIZE = 64
ENTER_BOOTLOADER_OUTPUT = bytes.fromhex("04 ee ee") + bytes(REPORT_SIZE - 3)
FLASH_BEGIN_REPORTS = (
    bytes.fromhex("01 aa 55") + bytes(REPORT_SIZE - 3),
    bytes.fromhex("02 aa 55") + bytes(REPORT_SIZE - 3),
    bytes.fromhex("03 aa 55 00 00 28 00 00 16 03") + bytes(REPORT_SIZE - 10),
)
FLASH_END_REPORT = bytes.fromhex("05 aa 55") + bytes(REPORT_SIZE - 3)

def load_patch_module(path):
    spec = importlib.util.spec_from_file_location("patch", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load patch module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def load_firmware(path, patch_name=None):
    image = path.read_bytes()
    if image[0] != 0x02:
        raise ValueError("firmware doesn't begin with the expected value. something is probably mega wrong")

    if patch_name is not None:
        patch_path = PATCH_DIR / f"{patch_name}.py"
        if not patch_path.is_file():
            raise FileNotFoundError(f"Patch not found: {patch_path}")
        patch_module = load_patch_module(patch_path)
        patch_fn = getattr(patch_module, "patch", None)
        if not callable(patch_fn):
            raise ValueError(f"Patch module must define patch(img): {patch_path}")

        # patches get firmware addresses since it's easier
        address_space = bytearray(BASE_ADDRESS) + bytearray(image)
        patched = patch_fn(address_space)
        if patched is not None:
            address_space = bytearray(patched)
        
        expected_size = BASE_ADDRESS + len(image)
        if len(address_space) != expected_size:
            raise ValueError(f"patch changed image size")
        
        image = bytes(address_space[BASE_ADDRESS:]) # remove zeros for actual flashing
        print(f"Applied patch: {patch_name} ({patch_path})")

    return image

def open_hid_device(path):
    device = hidapi.device()
    device.open_path(path)
    return device

def find_normal_vendor_interface(devices):
    for info in devices:
        if info.get("usage_page") == 0xFF1C and info.get("usage") == 0x92:
            return info
    # hidapi on linux doesn't always send usage metadata, so check the path end too
    if len(devices) == 2:
        for info in devices:
            path = info.get("path", b"")
            if info.get("interface_number") == 1 or path.endswith(b":1.1"):
                return info
    raise RuntimeError("could not find normal keyboard vendor HID collection")

def reports_for_image(image):
    checksum = sum(image)
    if checksum >= 1 << 24:
        raise ValueError("firmware checksum does not fit protocol's 24-bit field")

    protocol_reports = list(FLASH_BEGIN_REPORTS)
    protocol_reports.extend(
        image[offset : offset + REPORT_SIZE]
        for offset in range(0, len(image), REPORT_SIZE)
    )
    terminator = bytearray(REPORT_SIZE)
    terminator[:3] = bytes.fromhex("04 aa 55")
    terminator[8:11] = checksum.to_bytes(3, "little")
    protocol_reports.extend((bytes(terminator), FLASH_END_REPORT))
    return protocol_reports

def find_bootloader_feature_interface(devices):
    """find the bootloader interface 1's 'Consumer Control' collection"""
    for info in devices:
        if info.get("usage_page") == 0x000C and info.get("usage") == 0x0001:
            return info
    # hidapi on linux reports no metadata, so just do best effort with the end of the path
    for info in devices:
        path = info.get("path", b"")
        if info.get("interface_number") == 1 or path.endswith(b":1.1"):
            return info
    raise RuntimeError("could not find bootloader feature report collection")

def flash_firmware(devices, reports):
    device_info = find_bootloader_feature_interface(devices)
    device = open_hid_device(device_info["path"])
    try:
        sent = 0

        def send(report):
            nonlocal sent
            result = device.send_feature_report(b"\x00" + report)
            if result < 0:
                raise RuntimeError(
                    f"SET_FEATURE report {sent + 1} failed (hidapi={result}); stopping"
                )
            sent += 1
            if sent % 50 == 0 or sent == len(reports):
                print(f"Sent {sent}/{len(reports)} SET_FEATURE reports", flush=True)

        def poll():
            try:
                response = device.get_feature_report(0, REPORT_SIZE)
                print("this probably won't run. this poll usually fails")
            except OSError as exc:
                # this poll is required but will fail. i don't even know
                pass

        # i don't know why, but the first three and last two requests need to be slower and poll
        for report in reports[:3]:
            send(report)
            time.sleep(0.1)
            poll()
            time.sleep(0.1)

        payload_reports = reports[3:-2]
        for index, report in enumerate(payload_reports):
            send(report)
            if index + 1 < len(payload_reports):
                # the flasher i analyzed waits ~31ms here, but it turns out that's not requried. we'll wait 5 for safety.
                time.sleep(0.005)

        send(reports[-2])
        time.sleep(0.1)
        poll()
        time.sleep(0.1)
        
        send(reports[-1])
        
        print(f"sent {sent} reports", flush=True)
    finally:
        device.close()

def wait_for_device(vid, pid, timeout, label):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        devices = hidapi.enumerate(vid, pid)
        if devices:
            print(f"detected {label}: {vid:04x}:{pid:04x}", flush=True)
            return devices
        time.sleep(0.25)
    raise RuntimeError(f"timed out waiting for {label} {vid:04x}:{pid:04x}")

def enter_bootloader():
    devices = hidapi.enumerate(VID, PID)
    if not devices:
        raise RuntimeError("normal keyboard not found and bootloader is not enumerated")
    
    interface = find_normal_vendor_interface(devices)
    device = open_hid_device(interface["path"])
    try:
        result = device.write(ENTER_BOOTLOADER_OUTPUT)
        if result < 0:
            raise RuntimeError(f"bootloader report failed: {result}")
        print(f"sent bootloader report ({result} bytes)", flush=True)
    finally:
        device.close()
    
    return wait_for_device(BOOT_VID, BOOT_PID, 120, "bootloader")

def finalize_update():
    devices = wait_for_device(VID, PID, 120, "normal keyboard")
    interface = find_normal_vendor_interface(devices)
    
    device = open_hid_device(interface["path"])
    try:
        # i don't think this matters but we'll send it anyway
        FINALIZE_OUTPUT = bytes.fromhex("04 47 00 0f 38") + bytes(REPORT_SIZE - 5)
        result = device.write(FINALIZE_OUTPUT)
        if result < 0:
            raise RuntimeError(f"finalization output report failed: hidapi={result}")
        print(f"sent finalization report ({result} bytes)", flush=True)
    finally:
        device.close()

def main():
    if len(sys.argv) > 3:
        raise SystemExit("usage: python fw.py [firmware.bin] [patch_name]")
    
    firmware_path = Path(sys.argv[1]) if len(sys.argv) >= 2 else BASE_FIRMWARE
    patch_name = sys.argv[2] if len(sys.argv) == 3 else None

    image = load_firmware(firmware_path, patch_name)
    
    print(f"firmware: {firmware_path.resolve()}")
    print(f"size: {len(image)} bytes, checksum: {sum(image):06x}")
    print(f"sha-256: {hashlib.sha256(image).hexdigest()}")
    print()
    
    reports = reports_for_image(image)

    boot_devices = hidapi.enumerate(BOOT_VID, BOOT_PID)
    print(f"bootloader HID collections for {BOOT_VID:04x}:{BOOT_PID:04x}: {len(boot_devices)}")
    if not boot_devices:
        boot_devices = enter_bootloader()

    # input(f"flash {firmware_path.name} to bootloader?")
    
    flash_firmware(boot_devices, reports)
    
    print("waiting for normal keyboard", flush=True)
    finalize_update()
    
    print("firmware update finished", flush=True)

if __name__ == "__main__":
    main()
