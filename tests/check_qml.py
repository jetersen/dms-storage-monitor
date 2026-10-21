#!/usr/bin/env python3
"""Compile plugin components against an exact installed Dux tree, without deployment."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

p = argparse.ArgumentParser()
p.add_argument("dux_root", type=Path, help="Installed share/quickshell/dms directory")
p.add_argument("--quickshell", default="quickshell")
args = p.parse_args()
repo = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix=".qml-check-", dir=repo) as temp:
    root = Path(temp)
    # qs.* imports resolve against the test shell root. All shared components are
    # the exact installed files, not local source or compatibility substitutes.
    for path in args.dux_root.iterdir():
        if path.is_dir():
            (root / path.name).symlink_to(path, target_is_directory=True)
    (root / "Plugin").symlink_to(repo, target_is_directory=True)
    (root / "shell.qml").write_text('''import QtQuick
import Quickshell
import Quickshell.Io
import "./Plugin" as Plugin
import "./Plugin/services" as PluginServices
ShellRoot {
    id: root
    property string resultText: ""
    property int resultCode: 0
    Process {
        id: report
        command: ["sh", "-c", 'printf "%s" "$1" > "$2"', "storage-check", root.resultText, RESULT_PATH]
        onExited: Qt.exit(root.resultCode)
    }
    property Component widget: Component { Plugin.StorageMonitorWidget {} }
    property Component settings: Component { Plugin.StorageMonitorSettings {} }
    property Component popout: Component { Plugin.StorageMonitorPopout {} }
    property var storage: PluginServices.StorageService
    Timer {
        interval: 200
        running: true
        repeat: true
        onTriggered: {
            if (storage.probing) return;
            if (storage.lastError) {
                root.resultText = "STORAGE_CHECK_FAILED: " + storage.lastError + " path=" + storage.backendPath;
                root.resultCode = 1;
                report.running = true;
                return;
            }
            root.resultText = "STORAGE_QML_CHECK_OK " + JSON.stringify(storage.partitions);
            report.running = true;
        }
    }
}
'''.replace('RESULT_PATH', json.dumps(str(root / 'result.txt'))))
    env = dict(os.environ, QT_QPA_PLATFORM="wayland")
    result = subprocess.run([args.quickshell, "--path", str(root / "shell.qml"), "--no-color"],
                            env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=40)
    print(result.stdout)
    report = (root / "result.txt").read_text() if (root / "result.txt").exists() else ""
    print(report)
    if result.returncode or "STORAGE_QML_CHECK_OK" not in report:
        raise SystemExit(result.returncode or 1)
