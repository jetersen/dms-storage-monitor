#!/usr/bin/env python3
"""Read-only discovery uses Python stdlib. Only explicit actions import Gio."""
import importlib.util
import json
import math
import os
import subprocess
import sys

PREFIX = "org.freedesktop.UDisks2."
BLOCK, FS, ENC = (PREFIX + name for name in ("Block", "Filesystem", "Encrypted"))
DRIVE = PREFIX + "Drive"
SERVICE = "org.freedesktop.UDisks2"
BASE = "/org/freedesktop/UDisks2"
SYSTEM_PATHS = ("/boot", "/efi", "/home", "/nix", "/usr", "/var", "/srv", "/etc", "/opt", "/root")
MEMBERS = {"swap", "LVM2_member", "linux_raid_member", "zfs_member", "crypto_LUKS"}


def command_json(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
    return json.loads(result.stdout)


def walk(items, child="children"):
    for item in items:
        yield item
        yield from walk(item.get(child, []), child)


def decode_path(value):
    return bytes(value or []).rstrip(b"\0").decode("utf-8", "surrogateescape")


def read_objects():
    raw = command_json(["busctl", "--system", "--json=short", "call", SERVICE, BASE,
                        "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"])
    return {path: {interface: {key: value["data"] for key, value in props.items()}
                   for interface, props in interfaces.items()}
            for path, interfaces in raw["data"][0].items()}


def protected_mount(path):
    return path == "/" or any(path == p or path.startswith(p + "/") for p in SYSTEM_PATHS)


def power_off_state(objects, drive_path):
    """Validate the entire physical device, including UDisks sibling drives."""
    drive = objects.get(drive_path, {}).get(DRIVE, {})
    if not drive:
        raise ActionError("Drive is unavailable. Refresh first.")
    sibling = drive.get("SiblingId")
    drives = {p: i[DRIVE] for p, i in objects.items() if DRIVE in i
              and (p == drive_path or (sibling and i[DRIVE].get("SiblingId") == sibling))}
    if any(not d.get("CanPowerOff") or d.get("ConnectionBus") != "usb" or d.get("Configuration") or not d.get("TimeDetected")
           for d in drives.values()):
        raise ActionError("This physical device cannot be safely powered off here.")
    blocks = {p: i for p, i in objects.items() if i.get(BLOCK, {}).get("Drive") in drives}
    if not blocks:
        raise ActionError("Drive has no available block devices.")
    for path, props in objects.items():
        block = props.get(BLOCK, {})
        if block.get("CryptoBackingDevice") in blocks:
            raise ActionError("Lock encrypted volumes before powering off this device.")
        if props.get(PREFIX + "Partition", {}).get("Table") in blocks and path not in blocks:
            raise ActionError("Drive membership is incomplete. Refresh first.")
    for path, props in blocks.items():
        guard_object(objects, path)
        block = props[BLOCK]
        if props.get(PREFIX + "Partition", {}).get("Table") not in (None, "/", *blocks):
            raise ActionError("Drive membership is incomplete. Refresh first.")
        if (block.get("IdType") in MEMBERS - {"crypto_LUKS"}
                or block.get("MDRaid", "/") != "/" or block.get("MDRaidMember", "/") != "/"
                or PREFIX + "Block.LVM2" in props or PREFIX + "PhysicalVolume" in props
                or props.get(ENC, {}).get("CleartextDevice", "/") != "/"):
            raise ActionError("Nested or active storage must be managed externally.")
    # Mount state is deliberately excluded: unmounting must not change identity.
    return {
        "drives": [[p] + [d.get(k) for k in ("Id", "Serial", "WWN", "TimeDetected", "SiblingId")]
                   for p, d in sorted(drives.items())],
        "blocks": [[p] + [i[BLOCK].get(k) for k in ("DeviceNumber", "Size", "IdUUID", "IdType")]
                   for p, i in sorted(blocks.items())],
    }


def inventory(lsblk, findmnt, objects=None, statvfs=os.statvfs, realpath=os.path.realpath):
    """Join by block source aliases, never mount st_dev (Btrfs uses anonymous IDs)."""
    objects = objects or {}
    nodes, parents, aliases = {}, {}, {}

    def add(device, parent=None):
        key = device.get("kname") or device["name"]
        nodes.setdefault(key, device)
        parents.setdefault(key, set())
        if parent:
            parents[key].add(parent)
        for child in device.get("children", []):
            add(child, key)
    for device in lsblk.get("blockdevices", []):
        add(device)
    for key, node in nodes.items():
        names = ["/dev/" + key, "/dev/" + node["name"], "/dev/mapper/" + node["name"]]
        for name in names:
            aliases[realpath(name)] = key

    udisks = {}
    for path, interfaces in objects.items():
        block = interfaces.get(BLOCK, {})
        key = aliases.get(realpath(decode_path(block.get("Device"))))
        if key:
            udisks[key] = (path, interfaces)
            node = nodes[key]
            for source, target in (("IdType", "fstype"), ("IdLabel", "label"), ("IdUUID", "uuid")):
                if not node.get(target):
                    node[target] = block.get(source, "")
    mounts = {key: [] for key in nodes}
    for mount in walk(findmnt.get("filesystems", [])):
        # findmnt adds [/subvolume] or [/bind/source] to block device sources.
        source = mount.get("source", "").split("[", 1)[0]
        key = aliases.get(realpath(source)) if source.startswith("/dev/") else None
        if key and mount.get("target", "").startswith("/"):
            mounts[key].append(dict(mount, verified=True))
    for key, node in nodes.items():
        existing = {m["target"] for m in mounts[key]}
        for point in node.get("mountpoints", [node.get("mountpoint")]) or []:
            if point and point.startswith("/") and point not in existing:
                mounts[key].append({"target": point, "fstype": node.get("fstype") or ""})

    def ancestors(key, seen=None):
        seen = set() if seen is None else seen
        for parent in parents[key] - seen:
            seen.add(parent)
            ancestors(parent, seen)
        return seen

    encrypted = {key for key, node in nodes.items() if node.get("fstype") == "crypto_LUKS"}
    # crypt topology is authoritative even when udev/lsblk filesystem metadata is absent.
    for key, node in nodes.items():
        if node.get("type") == "crypt":
            encrypted.update(parents[key])

    def drive_for(key):
        paths = {udisks.get(k, ("", {}))[1].get(BLOCK, {}).get("Drive")
                 for k in ancestors(key) | {key}} - {None, "", "/"}
        return next(iter(paths)) if len(paths) == 1 else ""

    power_info = {}
    for drive_path in {drive_for(k) for k in nodes} - {""}:
        try:
            state = power_off_state(objects, drive_path)
            group = {d[0] for d in state["drives"]}
            keys = {k for k in nodes if drive_for(k) in group}
            if (any(nodes[k].get("type") not in ("disk", "part", "rom")
                    or (nodes[k].get("fstype") in MEMBERS and k not in encrypted)
                    or any(protected_mount(m["target"]) for m in mounts[k]) for k in keys)
                    or {udisks.get(k, ("", {}))[0] for k in keys} != {b[0] for b in state["blocks"]}):
                continue
            drive = objects[drive_path][DRIVE]
            power_info[drive_path] = ("sibling:" + drive["SiblingId"] if drive.get("SiblingId") else drive_path, state)
        except ActionError:
            continue
    result = []
    for key, node in nodes.items():
        if node.get("type") == "loop":
            continue
        children = {child for child in nodes if key in parents[child]}
        is_container = key in encrypted
        if is_container and children:
            continue
        entries = sorted(mounts[key], key=lambda m: (m["target"] != "/", len(m["target"]), m["target"]))
        fstype = next((m.get("fstype") for m in entries if m.get("fstype")), node.get("fstype") or "")
        if not is_container and (fstype in MEMBERS or not (entries or (fstype and not children))):
            continue
        path, interfaces = udisks.get(key, ("", {}))
        backing = sorted(ancestors(key) & encrypted)
        related = ancestors(key) | {key}
        unsafe = any(protected_mount(m["target"]) for k in related for m in mounts[k])
        # Mapped/LVM and system-managed layouts are monitor-only, except direct crypt FS.
        simple = node.get("type") in ("disk", "part") or (node.get("type") == "crypt" and len(backing) == 1 and parents[key] == set(backing))
        for k in related:
            props = udisks.get(k, ("", {}))[1]
            block = props.get(BLOCK, {})
            unsafe |= bool(block.get("Configuration") or block.get("HintIgnore") or block.get("HintSystem") or props.get(ENC, {}).get("ChildConfiguration"))
        manageable = bool(path and simple and not unsafe and not children)
        drive_path = drive_for(key)
        physical_group, power_identity = power_info.get(drive_path, ("", None))
        row = dict(name=node["name"], path="/dev/" + key, size=int(node.get("size") or 0),
                   deviceType=node.get("type") or "", drivePath=drive_path,
                   physicalGroup=physical_group, canPowerOff=bool(power_identity), powerOffIdentity=power_identity,
                   fstype=fstype, partTypeName=node.get("parttypename") or "", label=node.get("label") or node.get("partlabel") or "",
                   mountpoint=entries[0]["target"] if entries else "", mountpoints=[m["target"] for m in entries],
                   mounted=bool(entries), usageKnown=False, used=0, avail=0, usePercent=0,
                   encrypted=is_container or bool(backing), locked=is_container, manageable=manageable,
                   canLock=manageable and bool(backing), objectPath=path,
                   identity=[key, node.get("maj:min"), node.get("uuid") or "", int(node.get("size") or 0)],
                   backingPath=udisks.get(backing[0], ("", {}))[0] if len(backing) == 1 else "")
        for mount in entries:
            # lsblk-only mount hints preserve mounted state, but cannot verify
            # that statvfs at this path belongs to this block source.
            if not mount.get("verified"):
                continue
            try:
                stats = statvfs(mount["target"])
                size = stats.f_blocks * stats.f_frsize
                used = (stats.f_blocks - stats.f_bfree) * stats.f_frsize
                avail = stats.f_bavail * stats.f_frsize
                row.update(size=size, used=used, avail=avail, usageKnown=True,
                           usePercent=math.ceil(100 * used / (used + avail)) if used + avail else 0)
                break
            except OSError:
                continue
        result.append(row)
    return result


def probe():
    lsblk = command_json(["lsblk", "--json", "--bytes", "--tree", "--output",
                          "NAME,KNAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,LABEL,PARTLABEL,MAJ:MIN,UUID,PARTTYPENAME"])
    mounts = command_json(["findmnt", "--json", "--output", "SOURCE,TARGET,FSTYPE,MAJ:MIN"])
    try:
        objects = read_objects()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        objects = {}
    return {"partitions": inventory(lsblk, mounts, objects),
            "actionsAvailable": importlib.util.find_spec("gi") is not None}


class ActionError(Exception):
    pass


def current_row(request, probe_fn, power_off=False):
    row = next((p for p in probe_fn()["partitions"] if p["identity"] == request.get("identity")), None)
    if not row or not row["canPowerOff" if power_off else "manageable"] or row["objectPath"] != request.get("objectPath"):
        raise ActionError("Device changed or is managed by the system. Refresh and try again.")
    return row


class UDisks:
    def __init__(self):
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
        except (ImportError, ValueError):
            raise ActionError("Storage actions need Python PyGObject (Gio). Monitoring does not.") from None
        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def objects(self):
        return self.bus.call_sync(SERVICE, BASE, "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
                                  None, None, self.Gio.DBusCallFlags.NONE, 15000, None).unpack()[0]

    def call(self, path, interface, method, signature="(a{sv})", args=None):
        return self.bus.call_sync(SERVICE, path, interface, method,
                                  self.GLib.Variant(signature, args if args is not None else ({},)), None,
                                  self.Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, 120000, None).unpack()


def guard_object(objects, path, row=None):
    props = objects.get(path, {})
    block = props.get(BLOCK, {})
    if not block or block.get("Configuration") or block.get("HintIgnore") or block.get("HintSystem") or props.get(ENC, {}).get("ChildConfiguration"):
        raise ActionError("System-managed or unavailable device; use your system disk manager.")
    if row:
        number = block.get("DeviceNumber", -1)
        expected = row["identity"][1]
        if (expected != f"{os.major(number)}:{os.minor(number)}"
                or block.get("Size") != row["identity"][3]
                or (row["identity"][2] and block.get("IdUUID") != row["identity"][2])):
            raise ActionError("Device identity changed. Refresh and try again.")
    for mount in props.get(FS, {}).get("MountPoints", []):
        if protected_mount(decode_path(mount)):
            raise ActionError("System filesystems cannot be changed here.")
    return props


def perform_action(request, backend, probe_fn=probe):
    verb = request.get("verb")
    if verb == "power-off":
        return perform_power_off(request, backend, probe_fn)
    if verb not in ("mount", "unmount", "unlock", "lock"):
        raise ActionError("Unsupported storage operation.")
    row = current_row(request, probe_fn)
    path = row["objectPath"]
    objects = backend.objects()
    props = guard_object(objects, path, row)
    backing = row["backingPath"]
    if backing:
        guard_object(objects, backing)
        if props[BLOCK].get("CryptoBackingDevice") != backing or objects[backing].get(ENC, {}).get("CleartextDevice") != path:
            raise ActionError("Encrypted mapping changed. Refresh and try again.")
    if verb == "unlock":
        if not row["locked"] or ENC not in props or props[ENC].get("CleartextDevice", "/") != "/":
            raise ActionError("Encrypted device is already unlocked or unavailable.")
        secret = request.pop("passphrase", "")
        if not isinstance(secret, str) or not secret:
            raise ActionError("Enter a passphrase in the Storage Monitor dialog.")
        cleartext = backend.call(path, ENC, "Unlock", "(sa{sv})", (secret, {}))[0]
        secret = None
        # Only follow the exact object returned by Unlock. Never guess a mapper name.
        props = guard_object(backend.objects(), cleartext)
        if props[BLOCK].get("CryptoBackingDevice") != path or FS not in props:
            return "Unlocked. Nested storage must be mounted with your system disk manager."
        backend.call(cleartext, FS, "Mount")
        return "Unlocked and mounted."
    if row["locked"] or FS not in props:
        raise ActionError("No directly manageable filesystem on this device.")
    if verb == "mount":
        if props[FS].get("MountPoints"):
            raise ActionError("Filesystem is already mounted. Refresh first.")
        backend.call(path, FS, "Mount")
        return "Mounted."
    if verb == "lock" and (not row["canLock"] or not backing):
        raise ActionError("Nested encrypted storage must be managed externally.")
    if props[FS].get("MountPoints"):
        backend.call(path, FS, "Unmount")  # No force option.
    if verb == "lock":
        refreshed = current_row(request, probe_fn)
        objects = backend.objects()
        props = guard_object(objects, path, refreshed)
        container = guard_object(objects, backing)
        if refreshed["mounted"] or props[FS].get("MountPoints") or container[ENC].get("CleartextDevice") != path or props[BLOCK].get("CryptoBackingDevice") != backing:
            raise ActionError("Mapping or mount state changed; device was not locked.")
        # Refuse other mappings/consumers, even if the row previously looked simple.
        if any(p != path and i.get(BLOCK, {}).get("CryptoBackingDevice") == backing for p, i in objects.items()):
            raise ActionError("Other encrypted consumers exist; device was not locked.")
        backend.call(backing, ENC, "Lock")
        return "Unmounted and locked."
    return "Unmounted."


def perform_power_off(request, backend, probe_fn):
    def validate():
        row = current_row(request, probe_fn, power_off=True)
        objects = backend.objects()
        state = power_off_state(objects, row["drivePath"])
        if (row["drivePath"] != request.get("drivePath")
                or state != request.get("powerOffIdentity") or row["powerOffIdentity"] != state):
            raise ActionError("Physical device changed. Refresh and confirm removal again.")
        guard_object(objects, row["objectPath"], row)
        return row, objects, state

    row, objects, state = validate()
    members = [b[0] for b in state["blocks"]]
    for path in members:
        row, objects, _ = validate()
        if objects[path].get(FS, {}).get("MountPoints"):
            backend.call(path, FS, "Unmount")  # No force; any failure aborts power-off.
    row, objects, _ = validate()
    if any(objects[p].get(FS, {}).get("MountPoints") for p in members):
        raise ActionError("A filesystem was remounted; device was not powered off.")
    backend.call(row["drivePath"], DRIVE, "PowerOff")
    return "Unmounted and powered off."


def main():
    try:
        if sys.argv[1:] == ["probe"]:
            result = probe()
        elif sys.argv[1:] == ["action"]:
            # One JSON line over a private pipe. Secrets never appear in argv/env/logs.
            request = json.loads(sys.stdin.readline(65536))
            result = {"message": perform_action(request, UDisks())}
        else:
            raise ActionError("Invalid backend command.")
        print(json.dumps(result))
    except ActionError as error:
        print(json.dumps({"error": str(error)}))
        return 1
    except Exception:
        # D-Bus errors may echo method arguments. Never print exception details.
        print(json.dumps({"error": "Storage operation failed or was cancelled. Refresh and check system authorization."
                          if sys.argv[1:] == ["action"] else "Storage discovery failed. Check lsblk, findmnt and Python 3."}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
