import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("storage_backend", ROOT / "services/storage_backend.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def fixture(name):
    return json.loads((ROOT / "tests/fixtures" / (name + ".json")).read_text())


def stats(path):
    return SimpleNamespace(f_blocks=1000, f_frsize=4096, f_bfree=750, f_bavail=700)


def rows(name, statvfs=stats):
    f = fixture(name)
    aliases = f.get("aliases", {})
    return b.inventory(f["lsblk"], f["findmnt"], f.get("objects"), statvfs, lambda p: aliases.get(p, p))


class DiscoveryTests(unittest.TestCase):
    def test_framework_null_types_nested_luks_lvm_btrfs(self):
        found = rows("framework")
        self.assertEqual([r["name"] for r in found], ["nvme0n1p1", "system-root"])
        root = found[1]
        self.assertTrue(root["encrypted"])
        self.assertFalse(root["locked"])
        self.assertTrue(root["mounted"] and root["usageKnown"])
        self.assertEqual(root["fstype"], "btrfs")
        self.assertEqual(root["used"], 250 * 4096)
        self.assertEqual(len(root["mountpoints"]), 3)
        self.assertFalse(root["manageable"])

    def test_monitoring_needs_no_udisks_metadata(self):
        f = fixture("framework")
        f["objects"] = {}
        found = b.inventory(f["lsblk"], f["findmnt"], {}, stats, lambda p: f["aliases"].get(p, p))
        self.assertEqual(len(found), 2)
        self.assertTrue(found[1]["usageKnown"])
        self.assertTrue(found[1]["encrypted"])

    def test_locked_luks_is_not_a_filesystem(self):
        row, = rows("locked")
        self.assertTrue(row["locked"] and row["encrypted"])
        self.assertFalse(row["mounted"] or row["usageKnown"])
        self.assertTrue(row["manageable"])

    def test_udisks_identifies_locked_when_lsblk_metadata_missing(self):
        f = fixture("locked")
        f["lsblk"]["blockdevices"][0]["fstype"] = None
        found = b.inventory(f["lsblk"], f["findmnt"], f["objects"], stats, lambda p: p)
        self.assertTrue(found[0]["locked"])

    def test_direct_mapper_alias_and_duplicate_topology(self):
        row, = rows("unlocked")
        self.assertTrue(row["encrypted"] and row["canLock"])
        self.assertTrue(row["mounted"] and row["usageKnown"])
        self.assertFalse(row["locked"])
        f = fixture("unlocked")
        f["lsblk"]["blockdevices"].append(copy.deepcopy(f["lsblk"]["blockdevices"][0]))
        self.assertEqual(len(b.inventory(f["lsblk"], f["findmnt"], f["objects"], stats, lambda p: f["aliases"].get(p,p))), 1)

    def test_usage_failure_preserves_mounted_state(self):
        def denied(path):
            raise PermissionError(path)
        row, = rows("unlocked", denied)
        self.assertTrue(row["mounted"])
        self.assertFalse(row["usageKnown"])
        self.assertEqual(row["used"], 0)

    def test_unverified_lsblk_hint_does_not_sample_an_unrelated_mount(self):
        f = fixture("unlocked")
        f["findmnt"] = {"filesystems": []}
        row, = b.inventory(f["lsblk"], f["findmnt"], f["objects"], stats, lambda p: f["aliases"].get(p,p))
        self.assertTrue(row["mounted"])
        self.assertFalse(row["usageKnown"])

    def test_normal_disks_skip_swap_members_and_loop(self):
        found = rows("normal")
        self.assertEqual([p["name"] for p in found], ["sdc1", "sdd"])
        self.assertFalse(found[0]["mounted"])
        self.assertTrue(found[1]["mounted"])

    def test_all_mountpoints_protect_system_and_configured_volumes(self):
        f = fixture("unlocked")
        f["findmnt"]["filesystems"].append({"source": "/dev/dm-8", "target": "/home", "fstype": "ext4"})
        row, = b.inventory(f["lsblk"], f["findmnt"], f["objects"], stats, lambda p: f["aliases"].get(p,p))
        self.assertFalse(row["manageable"])
        f = fixture("locked")
        next(iter(f["objects"].values()))[b.ENC]["ChildConfiguration"] = [["fstab", {}]]
        self.assertFalse(b.inventory(f["lsblk"], f["findmnt"], f["objects"], stats, lambda p:p)[0]["manageable"])

    def test_probe_survives_missing_busctl_or_udisksctl(self):
        f = fixture("framework")
        with patch.object(b, "command_json", side_effect=[f["lsblk"], f["findmnt"]]), patch.object(b, "read_objects", side_effect=FileNotFoundError), patch.object(b.shutil, "which", return_value=None), patch.object(b.os.path, "realpath", side_effect=lambda p:f["aliases"].get(p,p)), patch.object(b.os, "statvfs", side_effect=stats):
            result = b.probe()
        self.assertFalse(result["actionsAvailable"])
        self.assertEqual(len(result["partitions"]), 2)

    def test_action_dependencies_are_cli_and_metadata(self):
        for cli, metadata in ((True, True), (False, True), (True, False)):
            f = fixture("framework")
            with patch.object(b, "command_json", side_effect=[f["lsblk"], f["findmnt"]]), patch.object(b, "read_objects", return_value={b.BASE: {}} if metadata else {}), patch.object(b.shutil, "which", return_value="/bin/udisksctl" if cli else None), patch.object(b.os.path, "realpath", side_effect=lambda p:f["aliases"].get(p,p)), patch.object(b.os, "statvfs", side_effect=stats):
                result = b.probe()
            self.assertEqual(result["actionsAvailable"], cli and metadata)
            self.assertEqual(bool(result["actionDependencyError"]), not (cli and metadata))
            self.assertEqual(len(result["partitions"]), 2)

    def test_discovery_command_failure_is_reported(self):
        with patch.object(b, "probe", side_effect=subprocess.CalledProcessError(1, "lsblk")), patch.object(sys, "argv", ["backend", "probe"]), patch("builtins.print") as output:
            self.assertEqual(b.main(), 1)
        self.assertIn("Storage discovery failed", output.call_args.args[0])


class FakeBackend:
    def __init__(self, objects):
        self.state = copy.deepcopy(objects)
        self.calls = []
        self.unlock_result = "/cleartext"
        self.fail = ""

    def objects(self):
        return copy.deepcopy(self.state)

    def call(self, path, interface, method, signature="(a{sv})", args=None):
        self.calls.append((path, interface, method, signature, args))
        if method == self.fail:
            raise RuntimeError("do not expose method arguments")
        if method == "Unlock":
            return (self.unlock_result,)
        if method == "Unmount":
            self.state[path][b.FS]["MountPoints"] = []
        return ()


class ActionTests(unittest.TestCase):
    def setup_action(self, name, verb):
        row, = rows(name)
        request = dict(identity=row["identity"], objectPath=row["objectPath"], verb=verb)
        backend = FakeBackend(fixture(name)["objects"])
        def probe():
            refreshed = copy.deepcopy(row)
            refreshed["mounted"] = bool(backend.state[row["objectPath"]].get(b.FS, {}).get("MountPoints"))
            return {"partitions": [refreshed]}
        return row, request, backend, probe

    def test_unlock_mounts_only_exact_returned_object(self):
        row, request, backend, probe = self.setup_action("locked", "unlock")
        request["passphrase"] = "test-only"
        backend.state["/cleartext"] = {b.BLOCK: {"CryptoBackingDevice": row["objectPath"]}, b.FS: {"MountPoints": []}}
        self.assertEqual(b.perform_action(request, backend, probe), "Unlocked and mounted.")
        self.assertEqual([(c[0],c[2]) for c in backend.calls], [(row["objectPath"], "Unlock"), ("/cleartext", "Mount")])
        self.assertNotIn("passphrase", request)

    def test_unlock_nested_does_not_mount_arbitrary_descendant(self):
        row, request, backend, probe = self.setup_action("locked", "unlock")
        request["passphrase"] = "test-only"
        backend.state["/cleartext"] = {b.BLOCK: {"CryptoBackingDevice": row["objectPath"]}}
        message = b.perform_action(request, backend, probe)
        self.assertIn("Nested", message)
        self.assertEqual([c[2] for c in backend.calls], ["Unlock"])

    def test_lock_unmounts_without_force_then_locks_container(self):
        row, request, backend, probe = self.setup_action("unlocked", "lock")
        self.assertEqual(b.perform_action(request, backend, probe), "Unmounted and locked.")
        self.assertEqual([(c[0],c[2]) for c in backend.calls], [(row["objectPath"], "Unmount"), (row["backingPath"], "Lock")])
        self.assertIsNone(backend.calls[0][4])

    def test_unmount_failure_never_locks(self):
        row, request, backend, probe = self.setup_action("unlocked", "lock")
        backend.fail = "Unmount"
        with self.assertRaises(RuntimeError):
            b.perform_action(request, backend, probe)
        self.assertEqual([c[2] for c in backend.calls], ["Unmount"])

    def test_remount_or_mapping_change_prevents_lock(self):
        for change in ("mount", "mapping"):
            row, request, backend, probe = self.setup_action("unlocked", "lock")
            count = 0
            def changed_probe():
                nonlocal count
                count += 1
                result = probe()
                if count > 1:
                    if change == "mount":
                        result["partitions"][0]["mounted"] = True
                    else:
                        backend.state[row["backingPath"]][b.ENC]["CleartextDevice"] = "/changed"
                return result
            with self.assertRaises(b.ActionError):
                b.perform_action(request, backend, changed_probe)
            self.assertEqual([c[2] for c in backend.calls], ["Unmount"])

    def test_identity_mismatch_or_configuration_refuses_before_mutation(self):
        for change in ("identity", "configuration", "system", "number", "uuid"):
            row, request, backend, probe = self.setup_action("unlocked", "lock")
            if change == "identity":
                request["identity"] = ["stale"]
            elif change == "configuration":
                backend.state[row["backingPath"]][b.BLOCK]["Configuration"] = [["crypttab", {}]]
            elif change == "system":
                backend.state[row["objectPath"]][b.FS]["MountPoints"] = [list(b"/home\0")]
            elif change == "number":
                backend.state[row["objectPath"]][b.BLOCK]["DeviceNumber"] = os.makedev(8, 9)
            else:
                row["identity"][2] = "expected-uuid"
                request["identity"][2] = "expected-uuid"
                backend.state[row["objectPath"]][b.BLOCK]["IdUUID"] = "changed-uuid"
            with self.assertRaises(b.ActionError):
                b.perform_action(request, backend, probe)
            self.assertEqual(backend.calls, [])

    def test_errors_do_not_echo_secrets(self):
        with patch.object(b, "UDisks", side_effect=RuntimeError("secret-value")), patch.object(sys, "argv", ["backend", "action"]), patch.object(sys, "stdin", __import__("io").StringIO('{"passphrase":"secret-value"}\n')), patch("builtins.print") as output:
            self.assertEqual(b.main(), 1)
        self.assertNotIn("secret-value", output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
