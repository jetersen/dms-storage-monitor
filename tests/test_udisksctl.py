"""Exercise the CLI adapter with mocked processes, never real disk actions."""
import io
import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_storage_backend import b

BLOCK = b.BASE + '/block_devices/sdb1'
CLEAR = b.BASE + '/block_devices/dm_2d9'
DRIVE = b.BASE + '/drives/test_usb'


def unlocked_objects():
    return {
        BLOCK: {b.ENC: {'CleartextDevice': CLEAR}},
        CLEAR: {b.BLOCK: {'Device': list(b'/dev/dm-9\0'), 'CryptoBackingDevice': BLOCK}},
    }


class CliTests(unittest.TestCase):
    def test_actions_target_relative_udisks_objects_without_force(self):
        for path, interface, method, verb in (
            (BLOCK, b.FS, 'Mount', 'mount'), (BLOCK, b.FS, 'Unmount', 'unmount'),
            (BLOCK, b.ENC, 'Lock', 'lock'), (DRIVE, b.DRIVE, 'PowerOff', 'power-off'),
        ):
            with self.subTest(method=method), patch.object(b.subprocess, 'run') as run:
                self.assertEqual(b.UDisks().call(path, interface, method), ())
                command = run.call_args.args[0]
                self.assertEqual(command, [b.UDISKSCTL, verb, '--object-path', path.removeprefix(b.BASE + '/')])
                self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
                self.assertEqual(run.call_args.kwargs['env']['LC_ALL'], 'C')
                self.assertTrue(run.call_args.kwargs['check'])
                self.assertNotIn('--force', command)
                self.assertNotIn('--no-user-interaction', command)

    def test_unlock_uses_only_private_stdin_and_verifies_reported_mapping(self):
        secret = 'fixture-key-π\n'
        result = SimpleNamespace(stdout=b'Unlocked /dev/sdb1 as /dev/dm-9.\n')
        with patch.object(b.subprocess, 'run', return_value=result) as run, patch.object(b, 'read_objects', return_value=unlocked_objects()):
            self.assertEqual(b.UDisks().call(BLOCK, b.ENC, 'Unlock', '(sa{sv})', (secret, {})), (CLEAR,))
        self.assertEqual(run.call_args.args[0], [b.UDISKSCTL, 'unlock', '--object-path',
                                               'block_devices/sdb1', '--key-file', '/dev/stdin'])
        self.assertEqual(run.call_args.kwargs['input'], secret.encode('utf-8'))
        self.assertNotIn(secret, repr(run.call_args.args))
        self.assertNotIn(secret, repr(run.call_args.kwargs['env']))

    def test_unlock_refuses_unverified_output_or_mapping_without_followup_action(self):
        for change in ('output', 'backing', 'reverse-link', 'duplicate'):
            objects = unlocked_objects()
            output = b'Unlocked /dev/sdb1 as /dev/dm-9.\n'
            if change == 'output':
                output = b'Unexpected output\n'
            elif change == 'backing':
                objects[CLEAR][b.BLOCK]['CryptoBackingDevice'] = '/other'
            elif change == 'reverse-link':
                objects[BLOCK][b.ENC]['CleartextDevice'] = '/other'
            else:
                objects['/duplicate'] = objects[CLEAR]
            with self.subTest(change=change), patch.object(b.subprocess, 'run', return_value=SimpleNamespace(stdout=output)) as run, patch.object(b, 'read_objects', return_value=objects):
                with self.assertRaises(b.ActionError):
                    b.UDisks().call(BLOCK, b.ENC, 'Unlock', '(sa{sv})', ('fixture-key', {}))
                self.assertEqual(run.call_count, 1)

    def test_unsupported_method_or_object_refused_before_process(self):
        for path, interface, method in ((BLOCK, b.FS, 'Format'), (BLOCK, b.DRIVE, 'PowerOff'),
                                        ('block_devices/sdb1', b.FS, 'Mount'),
                                        (b.BASE + '/block_devices/../sdb1', b.FS, 'Mount')):
            with patch.object(b.subprocess, 'run') as run:
                with self.assertRaises(b.ActionError):
                    b.UDisks().call(path, interface, method)
                run.assert_not_called()

    def test_cli_failure_and_timeout_output_are_sanitized(self):
        secret = 'fixture-key-not-for-output'
        for error in (subprocess.CalledProcessError(1, ['udisksctl'], stderr=secret.encode()),
                      subprocess.TimeoutExpired(['udisksctl'], 120, output=secret.encode())):
            def action(request, backend):
                return backend.call(BLOCK, b.ENC, 'Unlock', '(sa{sv})', (request['passphrase'], {}))
            with patch.object(b, 'perform_action', side_effect=action), patch.object(b.subprocess, 'run', side_effect=error), patch.object(sys, 'argv', ['backend', 'action']), patch.object(sys, 'stdin', io.StringIO(json.dumps({'passphrase': secret}) + '\n')), patch('builtins.print') as output:
                self.assertEqual(b.main(), 1)
                self.assertNotIn(secret, output.call_args.args[0])
                self.assertIn('Storage operation failed', output.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
