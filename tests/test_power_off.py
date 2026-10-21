"""Whole-device removal is tested only with simulated UDisks objects."""
import copy
import os
import unittest

from test_storage_backend import b, FakeBackend, stats


def usb_fixture():
    drive = '/drives/usb'
    objects = {drive: {b.DRIVE: {
        'Id': 'test-usb', 'Serial': 'test-serial', 'TimeDetected': 100,
        'SiblingId': 'usb-port-1', 'ConnectionBus': 'usb', 'CanPowerOff': True,
    }}}
    disk = dict(name='sda', type='disk', size=64000, **{'maj:min': '8:0'})
    objects['/blocks/sda'] = {b.BLOCK: {
        'Device': list(b'/dev/sda\0'), 'Drive': drive, 'DeviceNumber': os.makedev(8, 0), 'Size': 64000,
    }, b.PREFIX + 'PartitionTable': {'Type': 'dos'}}
    disk['children'] = []
    mounts = []
    for n, label in ((1, 'Ventoy'), (2, 'VTOYEFI')):
        name, mount = f'sda{n}', f'/media/{label}'
        disk['children'].append(dict(name=name, type='part', size=16000, fstype='exfat',
                                     uuid=f'uuid-{n}', mountpoints=[mount], **{'maj:min': f'8:{n}'}))
        objects[f'/blocks/{name}'] = {
            b.BLOCK: {'Device': list(f'/dev/{name}\0'.encode()), 'Drive': drive,
                      'DeviceNumber': os.makedev(8, n), 'Size': 16000,
                      'IdUUID': f'uuid-{n}', 'IdType': 'exfat'},
            b.FS: {'MountPoints': [list((mount + '\0').encode())]},
            b.PREFIX + 'Partition': {'Table': '/blocks/sda'},
        }
        mounts.append(dict(source=f'/dev/{name}', target=mount, fstype='exfat'))
    return {'lsblk': {'blockdevices': [disk]}, 'findmnt': {'filesystems': mounts}, 'objects': objects}


def inventory(f, objects=None):
    return b.inventory(copy.deepcopy(f['lsblk']), f['findmnt'],
                       f['objects'] if objects is None else objects, stats, lambda p: p)


class PowerOffTests(unittest.TestCase):
    def setup_action(self):
        f = usb_fixture()
        row = inventory(f)[0]
        request = {k: copy.deepcopy(row[k]) for k in ('identity', 'objectPath', 'drivePath', 'powerOffIdentity')}
        request['verb'] = 'power-off'
        backend = FakeBackend(f['objects'])

        def probe():
            return {'partitions': inventory(f, backend.objects())}
        return f, request, backend, probe

    def test_discovery_groups_partitions_and_preserves_usage(self):
        rows = inventory(usb_fixture())
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['canPowerOff'] and r['manageable'] and r['usageKnown'] for r in rows))
        self.assertEqual(rows[0]['physicalGroup'], rows[1]['physicalGroup'])
        self.assertEqual(rows[0]['powerOffIdentity'], rows[1]['powerOffIdentity'])
        self.assertFalse(any(r['canPowerOff'] for r in inventory(usb_fixture(), {})))

    def test_unmounts_all_partitions_before_power_off_without_force(self):
        _, request, backend, probe = self.setup_action()
        self.assertEqual(b.perform_action(request, backend, probe), 'Unmounted and powered off.')
        self.assertEqual([(c[0], c[1], c[2]) for c in backend.calls], [
            ('/blocks/sda1', b.FS, 'Unmount'), ('/blocks/sda2', b.FS, 'Unmount'),
            ('/drives/usb', b.DRIVE, 'PowerOff')])
        self.assertTrue(all(c[4] is None for c in backend.calls))

    def test_unmount_failure_never_powers_off(self):
        _, request, backend, probe = self.setup_action()
        backend.fail = 'Unmount'
        with self.assertRaises(RuntimeError):
            b.perform_action(request, backend, probe)
        self.assertEqual([c[2] for c in backend.calls], ['Unmount'])

    def test_remount_or_system_mount_after_unmount_prevents_power_off(self):
        for point in ('/media/again', '/home'):
            with self.subTest(point=point):
                _, request, backend, probe = self.setup_action()
                original = backend.call

                def remount(*args, **kwargs):
                    result = original(*args, **kwargs)
                    if args[0] == '/blocks/sda2':
                        backend.state['/blocks/sda1'][b.FS]['MountPoints'] = [list((point+'\0').encode())]
                    return result
                backend.call = remount
                with self.assertRaises(b.ActionError):
                    b.perform_action(request, backend, probe)
                self.assertEqual([c[2] for c in backend.calls], ['Unmount', 'Unmount'])

    def test_stale_drive_or_member_identity_refused_before_mutation(self):
        for prop, path, interface in (
            ('TimeDetected', '/drives/usb', b.DRIVE), ('Serial', '/drives/usb', b.DRIVE),
            ('IdUUID', '/blocks/sda2', b.BLOCK), ('DeviceNumber', '/blocks/sda1', b.BLOCK),
        ):
            with self.subTest(prop=prop):
                _, request, backend, probe = self.setup_action()
                backend.state[path][interface][prop] = 999 if prop in ('TimeDetected', 'DeviceNumber') else 'changed'
                with self.assertRaises(b.ActionError):
                    b.perform_action(request, backend, probe)
                self.assertEqual(backend.calls, [])

    def test_member_change_after_first_unmount_aborts_rest(self):
        _, request, backend, probe = self.setup_action()
        original = backend.call

        def replace_member(*args, **kwargs):
            result = original(*args, **kwargs)
            backend.state['/blocks/sda2'][b.BLOCK]['IdUUID'] = 'replacement'
            return result
        backend.call = replace_member
        with self.assertRaises(b.ActionError):
            b.perform_action(request, backend, probe)
        self.assertEqual([c[2] for c in backend.calls], ['Unmount'])

    def test_protected_configured_internal_or_nested_device_refused(self):
        for change in ('system-mount', 'configured', 'hint', 'internal', 'unsupported', 'no-detection-time', 'lvm', 'swap', 'mapping', 'unknown-member'):
            with self.subTest(change=change):
                f, request, backend, probe = self.setup_action()
                if change == 'system-mount':
                    backend.state['/blocks/sda2'][b.FS]['MountPoints'] = [list(b'/nix\0')]
                elif change == 'configured':
                    backend.state['/blocks/sda2'][b.BLOCK]['Configuration'] = [['fstab', {}]]
                elif change == 'hint':
                    backend.state['/blocks/sda2'][b.BLOCK]['HintSystem'] = True
                elif change == 'internal':
                    backend.state['/drives/usb'][b.DRIVE]['ConnectionBus'] = 'nvme'
                elif change == 'unsupported':
                    backend.state['/drives/usb'][b.DRIVE]['CanPowerOff'] = False
                elif change == 'no-detection-time':
                    del backend.state['/drives/usb'][b.DRIVE]['TimeDetected']
                elif change in ('lvm', 'swap'):
                    backend.state['/blocks/sda2'][b.BLOCK]['IdType'] = 'LVM2_member' if change == 'lvm' else 'swap'
                elif change == 'mapping':
                    backend.state['/blocks/dm0'] = {b.BLOCK: {'CryptoBackingDevice': '/blocks/sda2'}}
                else:
                    backend.state['/blocks/extra'] = {b.BLOCK: {
                        'Device': list(b'/dev/unknown\0'), 'Drive': '/drives/usb'}}
                self.assertFalse(any(r['canPowerOff'] for r in probe()['partitions']))
                with self.assertRaises(b.ActionError):
                    b.perform_action(request, backend, probe)
                self.assertEqual(backend.calls, [])

    def test_sibling_optical_drive_is_included_and_monitored_read_only(self):
        f = usb_fixture()
        sibling = copy.deepcopy(f['objects']['/drives/usb'])
        sibling[b.DRIVE]['Id'] = 'test-optical'
        f['objects']['/drives/optical'] = sibling
        f['lsblk']['blockdevices'].append(dict(name='sr0', type='rom', fstype='iso9660', size=16000,
                                               mountpoints=['/media/cd'], **{'maj:min': '11:0'}))
        f['objects']['/blocks/sr0'] = {
            b.BLOCK: {'Device': list(b'/dev/sr0\0'), 'DeviceNumber': os.makedev(11, 0),
                      'Drive': '/drives/optical', 'Size': 16000, 'IdType': 'iso9660'},
            b.FS: {'MountPoints': [list(b'/media/cd\0')]}}
        rows = inventory(f)
        self.assertEqual([r['name'] for r in rows], ['sda1', 'sda2', 'sr0'])
        self.assertTrue(all(r['canPowerOff'] for r in rows))
        self.assertEqual(len({r['physicalGroup'] for r in rows}), 1)
        self.assertFalse(rows[-1]['manageable'])
        row = rows[0]
        request = {k: row[k] for k in ('identity', 'objectPath', 'drivePath', 'powerOffIdentity')}
        request['verb'] = 'power-off'
        backend = FakeBackend(f['objects'])
        b.perform_action(request, backend, lambda: {'partitions': inventory(f, backend.objects())})
        self.assertEqual([c[0] for c in backend.calls[:-1]], ['/blocks/sda1', '/blocks/sda2', '/blocks/sr0'])
        # A system volume on the sibling disqualifies the entire physical device.
        f['objects']['/blocks/sr0'][b.FS]['MountPoints'] = [list(b'/home\0')]
        self.assertFalse(any(r['canPowerOff'] for r in inventory(f)))


if __name__ == '__main__':
    unittest.main()
