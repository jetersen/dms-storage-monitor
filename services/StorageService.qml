pragma Singleton

import QtQuick
import Quickshell
import Quickshell.Io
import qs.Common
import qs.Services

Singleton {
    id: root

    // Runtime state
    property bool probing: false
    property bool busyAction: false
    property string lastError: ""
    property string lastActionMessage: ""

    // Mounted state and usage availability are independent.
    property var partitions: []

    // Settings supplied by the widget
    property int refreshInterval: 60
    property bool showUnmounted: true
    property bool showLabels: true
    property bool showSystemPartitions: false

    readonly property var systemPartitionLabels: ["SYSTEM", "ESP", "RECOVERY", "WINRE", "MSR", "DIAGS", "MYASUS"]

    readonly property var visiblePartitions: {
        const list = [];
        const partitions = root.partitions;
        for (let i = 0; i < partitions.length; i++) {
            const partition = partitions[i];
            if (root.showSystemPartitions || !isSystemPartition(partition))
                list.push(partition);
        }
        return list;
    }

    function isSystemPartition(partition) {
        if (!partition)
            return false;
        const mountpoint = partition.mountpoint || "";
        if (mountpoint === "/boot" || mountpoint.indexOf("/boot/") === 0)
            return true;
        const partType = (partition.partTypeName || "").toLowerCase();
        if (partType.indexOf("efi") >= 0 || partType.indexOf("recovery") >= 0 || partType.indexOf("reserved") >= 0)
            return true;
        const label = (partition.label || "").toUpperCase();
        return systemPartitionLabels.indexOf(label) >= 0;
    }

    readonly property string backendPath: decodeURIComponent(Qt.resolvedUrl("storage_backend.py").toString()).replace(/^file:\/\//, "")
    property bool actionsAvailable: false
    property string _actionRequest: ""

    Timer {
        id: refreshTimer
        interval: root.refreshInterval * 1000
        repeat: true
        running: true
        onTriggered: root.refresh()
    }

    Component.onCompleted: refresh()

    // Failed-to-start processes do not necessarily emit exited.
    Timer {
        interval: 1000
        repeat: true
        running: root.probing || root.busyAction
        onTriggered: {
            if (root.probing && !probeProc.running) {
                root.probing = false;
                root.lastError = "Could not start storage discovery. Check Python 3.";
            }
            if (root.busyAction && !actionProc.running) {
                root._actionRequest = "";
                root.busyAction = false;
                root.lastError = "Could not start storage action. Check Python 3.";
            }
        }
    }

    function refresh() {
        if (probeProc.running || busyAction)
            return;
        probing = true;
        lastError = "";
        probeProc.running = true;
    }

    function mountPartition(partition) { beginAction("mount", partition); }
    function unmountPartition(partition) { beginAction("unmount", partition); }
    function lockPartition(partition) { beginAction("lock", partition); }
    function unlockPartition(partition, passphrase) { beginAction("unlock", partition, passphrase); }

    function beginAction(verb, partition, passphrase) {
        if (busyAction || probing || !actionsAvailable || !partition || !partition.manageable)
            return;
        busyAction = true;
        lastError = "";
        lastActionMessage = "";
        _actionRequest = JSON.stringify({verb: verb, identity: partition.identity,
            objectPath: partition.objectPath, passphrase: passphrase || ""}) + "\n";
        actionProc.running = true;
    }

    function formatBytes(bytes) {
        const value = Number(bytes || 0);
        if (isNaN(value) || value <= 0)
            return "0 B";
        const units = ["B", "KB", "MB", "GB", "TB"];
        let index = 0;
        let size = value;
        while (size >= 1024 && index < units.length - 1) {
            size /= 1024;
            index++;
        }
        const digits = index === 0 ? 0 : (size >= 100 ? 0 : (size >= 10 ? 1 : 2));
        return size.toFixed(digits) + " " + units[index];
    }

    function totalStats() {
        let used = 0;
        let size = 0;
        let known = 0;
        const partitions = root.visiblePartitions;
        for (let i = 0; i < partitions.length; i++) {
            const p = partitions[i];
            if (!p.mounted || !p.usageKnown)
                continue;
            used += p.used;
            size += p.size;
            known++;
        }
        const percent = size > 0 ? Math.round((used / size) * 100) : 0;
        return { used: used, size: size, percent: percent, known: known };
    }

    Process {
        id: probeProc
        command: ["python3", root.backendPath, "probe"]
        stdout: StdioCollector {}
        stderr: StdioCollector {}
        onExited: (exitCode, exitStatus) => {
            root.probing = false;
            try {
                const data = JSON.parse(stdout.text);
                if (exitCode !== 0 || data.error) {
                    root.lastError = data.error || "Storage discovery failed.";
                    return;
                }
                root.partitions = data.partitions;
                root.actionsAvailable = data.actionsAvailable;
            } catch (e) {
                root.lastError = "Storage discovery failed. Check Python 3, lsblk and findmnt.";
            }
        }
    }

    Process {
        id: actionProc
        command: ["python3", root.backendPath, "action"]
        stdinEnabled: true
        // Action output is deliberately sanitized by the backend.
        stdout: StdioCollector {}
        onStarted: {
            actionProc.write(root._actionRequest);
            root._actionRequest = "";
        }
        onExited: (exitCode, exitStatus) => {
            root._actionRequest = "";
            root.busyAction = false;
            try {
                const data = JSON.parse(stdout.text);
                if (exitCode !== 0 || data.error)
                    root.lastError = data.error || "Storage operation failed.";
                else
                    root.lastActionMessage = data.message || "OK";
            } catch (e) {
                root.lastError = "Storage operation failed. Check Python 3 and system authorization.";
            }
            if (root.lastError)
                ToastService.showError("Storage Monitor", root.lastError);
            // Keep the error while refreshing the actual state, including partial success.
            root.probing = true;
            probeProc.running = true;
        }
    }
}
