# Storage Monitor for Dux Shell

A DankBar widget for mounted filesystem usage, including already unlocked LUKS volumes, LVM and Btrfs subvolumes. Monitoring does not need a passphrase, polkit authorization or Python PyGObject.

![Storage Monitor popout](assets/screenshot.png)

## Features

- Total usage for visible mounted filesystems with known usage
- One usage row per block filesystem, even with several subvolume or bind mounts
- Locked encrypted containers shown separately from unlocked filesystems
- Mounted volumes remain marked mounted if usage cannot be read
- Labels, system partition filtering and automatic refresh
- Optional mount, unmount, unlock and lock actions for simple user-managed volumes

## Requirements

- Dux Shell 2.0 or newer on Wayland
- Python 3 and util-linux (`lsblk`, `findmnt`) for monitoring
- Optional systemd `busctl` and udisks2 for filesystem metadata when `lsblk` cannot supply it, including detection of locked LUKS containers
- Optional Python PyGObject with Gio, `busctl`, udisks2 and a polkit agent for storage actions

Missing action dependencies do not prevent monitoring. The Python used by the shell must be able to import `gi` and load Gio; installing a system library alone may not make it available in that Python environment.

## Installation

Install this repository as `storageMonitor` in your Dux plugin directory, enable **Storage Monitor** in Settings → Plugins, and add it to DankBar. When managed by Nix or dotfiles, update the plugin source pin through that configuration's normal workflow.

## Usage

Click the storage icon to see filesystems, mount points and usage. Already unlocked and mounted encrypted storage is monitored automatically. Swap, loop devices and storage member containers do not contribute to usage totals. Btrfs usage comes from the filesystem, so shared subvolumes on the same block device count once.

When action dependencies are available, simple user-managed filesystems offer **Mount** or **Unmount**. Unmount requires a second click within three seconds. Locked LUKS containers offer **Unlock**, followed by a password field in the popout. **Unmount and lock** also requires confirmation and is available only for a direct encrypted filesystem.

Passphrases travel to the action backend through standard input and then to the system UDisks D-Bus API. They are not placed in commands, environment variables, temporary files or logs. Polkit authorizes the operation; the LUKS passphrase must still be entered in the popout. Cancel the system authorization dialog to cancel an action.

System mounts, configured system volumes and nested mapped layouts are monitor-only. Unlocking a nested LUKS layout leaves subsequent volume activation and mounting to the system disk manager. Locking never forces an unmount, and a failed unmount prevents locking. If unlock succeeds but mounting fails, refresh to inspect the unlocked device before retrying.

The backend uses the documented UDisks [Encrypted](https://storaged.org/doc/udisks2-api/latest/gdbus-org.freedesktop.UDisks2.Encrypted.html), [Block](https://storaged.org/doc/udisks2-api/latest/gdbus-org.freedesktop.UDisks2.Block.html) and [Filesystem](https://storaged.org/doc/udisks2-api/latest/gdbus-org.freedesktop.UDisks2.Filesystem.html) interfaces. It mounts only the exact cleartext object returned by Unlock. Filesystem support for actions depends on udisks2 and installed filesystem drivers.

## Development checks

```sh
python3 -m unittest discover -s tests -v
python3 services/storage_backend.py probe
python3 tests/check_qml.py /path/to/installed/share/quickshell/dms
```

The QML check requires a Wayland connection. It compiles all plugin components against the supplied installed Dux files and runs only the read-only monitoring service in a separate shell without windows. It does not load the desktop shell or invoke disk actions. Action regression tests use simulated D-Bus objects and calls; live unlock/mount/unmount/lock validation requires a disposable volume and separate authorization.

## License

MIT — see [LICENSE](LICENSE).
