import QtQuick
import QtQuick.Layouts
import qs.Common
import qs.Services
import qs.DCommon.Widgets
import qs.Modules.Plugins
import "./services" as Services

PopoutComponent {
    id: root

    headerText: "Storage Monitor"
    detailsText: root.storage.probing ? "Scanning disks…" : totalDescription()
    showCloseButton: true

    readonly property var storage: Services.StorageService
    property int maxContentHeight: 560
    property string pendingUnmount: ""
    property int confirmCountdown: 0
    property string pendingVerb: "unmount"
    property var unlockTarget: null

    function totalDescription() {
        const stats = root.storage.totalStats();
        if (stats.known === 0)
            return "No filesystem usage available";
        return root.storage.formatBytes(stats.used) + " used of " + root.storage.formatBytes(stats.size) + " · " + stats.percent + "%";
    }

    function displayName(partition) {
        if (root.storage.showLabels && partition.label)
            return partition.label;
        return partition.name;
    }

    function usageText(partition) {
        if (partition.locked)
            return "Locked · " + root.storage.formatBytes(partition.size);
        if (partition.mounted && !partition.usageKnown)
            return "Mounted · usage unavailable";
        if (!partition.mounted)
            return "Not mounted · " + root.storage.formatBytes(partition.size);
        return root.storage.formatBytes(partition.used) + " / " + root.storage.formatBytes(partition.size) + " · " + partition.usePercent + "%";
    }

    function fsIconName(partition) {
        const fs = partition.fstype;
        if (fs === "ntfs" || fs === "exfat" || fs === "vfat" || fs === "fat32")
            return "usb";
        return partition.encrypted ? "lock" : "storage";
    }

    function partitionColor(partition) {
        if (!partition.mounted)
            return Theme.surfaceVariantText;
        if (partition.usePercent >= 90)
            return Theme.error;
        if (partition.usePercent >= 75)
            return Theme.warning;
        return Theme.primary;
    }

    Component.onCompleted: storage.refresh()
    onVisibleChanged: {
        if (!visible) {
            passphraseField.clear();
            root.unlockTarget = null;
        }
    }

    function handleAction(partition, verb) {
        if (root.storage.busyAction || root.storage.probing || !partition.manageable)
            return;
        if (partition.locked) {
            root.unlockTarget = partition;
            return;
        }
        verb = verb || "unmount";
        if (partition.mounted || verb === "lock") {
            if (root.pendingUnmount === partition.path && root.pendingVerb === verb) {
                root.pendingUnmount = "";
                confirmResetTimer.stop();
                root.confirmCountdown = 0;
                if (verb === "lock")
                    root.storage.lockPartition(partition);
                else
                    root.storage.unmountPartition(partition);
            } else {
                root.pendingUnmount = partition.path;
                root.pendingVerb = verb;
                root.confirmCountdown = 3;
                confirmResetTimer.restart();
            }
        } else {
            root.storage.mountPartition(partition);
        }
    }

    Timer {
        id: confirmResetTimer
        interval: 1000
        repeat: true
        onTriggered: {
            root.confirmCountdown -= 1;
            if (root.confirmCountdown <= 0) {
                confirmResetTimer.stop();
                root.pendingUnmount = "";
            }
        }
    }

    Item {
        width: parent.width
        implicitHeight: Math.min(contentColumn.implicitHeight, root.maxContentHeight)
        height: implicitHeight

        DFlickable {
            anchors.fill: parent
            contentWidth: width
            contentHeight: contentColumn.implicitHeight
            clip: true

            Column {
                id: contentColumn
                width: parent.width
                spacing: Theme.spacingM

                // Overview card
                Rectangle {
                    width: parent.width
                    radius: Theme.cornerRadius
                    color: Theme.surfaceContainerHigh
                    border.color: Theme.outline
                    border.width: 1
                    implicitHeight: overviewColumn.implicitHeight + Theme.spacingM * 2

                    Column {
                        id: overviewColumn
                        width: parent.width - Theme.spacingM * 2
                        anchors.centerIn: parent
                        spacing: Theme.spacingS

                        RowLayout {
                            width: parent.width
                            spacing: Theme.spacingS
                            DIcon { name: "storage"; size: 20; color: Theme.primary }
                            StyledText {
                                Layout.fillWidth: true
                                text: "Mounted filesystems with known usage"
                                font.pixelSize: Theme.fontSizeMedium
                                font.weight: Font.Bold
                                color: Theme.surfaceText
                            }
                            StyledText {
                                text: root.storage.totalStats().known > 0 ? root.storage.totalStats().percent + "%" : "—"
                                font.pixelSize: Theme.fontSizeMedium
                                font.weight: Font.Bold
                                color: root.storage.totalStats().percent >= 90 ? Theme.error : Theme.primary
                            }
                        }

                        Rectangle {
                            width: parent.width
                            height: 8
                            radius: 4
                            color: Theme.surfaceContainer

                            Rectangle {
                                width: Math.max(4, parent.width * root.storage.totalStats().percent / 100)
                                height: parent.height
                                radius: 4
                                color: root.storage.totalStats().percent >= 90 ? Theme.error : Theme.primary
                            }
                        }

                        StyledText {
                            width: parent.width
                            text: root.storage.totalStats().known > 0 ? root.storage.formatBytes(root.storage.totalStats().used) + " used · " + root.storage.formatBytes(root.storage.totalStats().size - root.storage.totalStats().used) + " free" : "Usage unavailable"
                            font.pixelSize: Theme.fontSizeSmall
                            color: Theme.surfaceVariantText
                        }
                    }
                }

                // Error banner
                Rectangle {
                    width: parent.width
                    radius: Theme.cornerRadius
                    color: Theme.errorHover
                    visible: root.storage.lastError.length > 0
                    implicitHeight: errorText.implicitHeight + Theme.spacingM * 2

                    StyledText {
                        id: errorText
                        anchors.fill: parent
                        anchors.margins: Theme.spacingM
                        text: root.storage.lastError
                        font.pixelSize: Theme.fontSizeSmall
                        color: Theme.error
                        wrapMode: Text.WordWrap
                    }
                }

                StyledText {
                    width: parent.width
                    visible: root.storage.lastActionMessage.length > 0
                    text: root.storage.lastActionMessage
                    wrapMode: Text.WordWrap
                    color: Theme.surfaceVariantText
                }

                Column {
                    width: parent.width
                    spacing: Theme.spacingS
                    visible: root.unlockTarget !== null
                    StyledText {
                        text: root.unlockTarget ? "Unlock " + root.displayName(root.unlockTarget) : ""
                        color: Theme.surfaceText
                    }
                    DTextField {
                        id: passphraseField
                        width: parent.width
                        echoMode: TextInput.Password
                        placeholderText: "Passphrase"
                        onAccepted: unlockButton.clicked()
                    }
                    Row {
                        spacing: Theme.spacingS
                        DButton {
                            id: unlockButton
                            text: "Unlock and mount"
                            enabled: !root.storage.busyAction && !root.storage.probing && passphraseField.text.length > 0
                            onClicked: {
                                if (!enabled || !root.unlockTarget)
                                    return;
                                root.storage.unlockPartition(root.unlockTarget, passphraseField.text);
                                passphraseField.clear();
                                root.unlockTarget = null;
                            }
                        }
                        DButton {
                            text: "Cancel"
                            onClicked: {
                                passphraseField.clear();
                                root.unlockTarget = null;
                            }
                        }
                    }
                }

                // Partitions list header + refresh
                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingS

                    StyledText {
                        Layout.fillWidth: true
                        text: "Partitions"
                        font.pixelSize: Theme.fontSizeMedium
                        font.weight: Font.Bold
                        color: Theme.surfaceText
                    }

                    Rectangle {
                        Layout.preferredWidth: 28
                        Layout.preferredHeight: 28
                        radius: 14
                        color: refreshMouse.containsMouse ? Theme.surfaceContainerHighest : "transparent"
                        DIcon {
                            anchors.centerIn: parent
                            name: "refresh"
                            size: 15
                            color: Theme.surfaceText
                            RotationAnimation on rotation {
                                running: root.storage.probing
                                from: 0; to: 360; duration: 900; loops: Animation.Infinite
                            }
                        }
                        MouseArea {
                            id: refreshMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.storage.refresh()
                        }
                    }
                }

                // Partition cards
                Repeater {
                    model: {
                        const list = root.storage.visiblePartitions;
                        if (!root.storage.showUnmounted)
                            return list.filter(p => p.mounted);
                        return list;
                    }
                    delegate: Rectangle {
                        width: parent.width
                        radius: Theme.cornerRadius
                        color: Theme.surfaceContainer
                        border.width: 1
                        border.color: Theme.outline
                        implicitHeight: cardColumn.implicitHeight + Theme.spacingM * 2

                        Column {
                            id: cardColumn
                            width: parent.width - Theme.spacingM * 2
                            anchors.centerIn: parent
                            spacing: Theme.spacingS

                            RowLayout {
                                width: parent.width
                                spacing: Theme.spacingM

                                Rectangle {
                                    Layout.preferredWidth: 34
                                    Layout.preferredHeight: 34
                                    radius: 17
                                    color: modelData.mounted ? Theme.primaryContainer : Theme.surfaceContainerHigh
                                    DIcon {
                                        anchors.centerIn: parent
                                        name: root.fsIconName(modelData)
                                        size: 18
                                        color: modelData.mounted ? Theme.primary : Theme.surfaceVariantText
                                    }
                                }

                                Column {
                                    Layout.fillWidth: true
                                    spacing: 2

                                    Row {
                                        spacing: Theme.spacingS
                                        StyledText {
                                            text: root.displayName(modelData)
                                            font.pixelSize: Theme.fontSizeMedium
                                            font.weight: Font.Bold
                                            color: Theme.surfaceText
                                        }
                                        Rectangle {
                                            height: 18
                                            radius: 4
                                            color: Theme.surfaceContainerHigh
                                            visible: root.storage.showLabels && modelData.label.length > 0
                                            StyledText {
                                                anchors.centerIn: parent
                                                text: modelData.label
                                                font.pixelSize: 9
                                                color: Theme.surfaceVariantText
                                            }
                                            width: modelData.label.length * 6 + 12
                                        }
                                    }

                                    StyledText {
                                        width: parent.width
                                        text: modelData.path + (modelData.mounted ? " → " + modelData.mountpoint : " · " + modelData.fstype.toUpperCase())
                                        font.pixelSize: 10
                                        color: Theme.surfaceVariantText
                                        elide: Text.ElideMiddle
                                    }
                                }

                                Rectangle {
                                    visible: modelData.manageable && root.storage.actionsAvailable
                                    Layout.preferredWidth: {
                                        if ((root.pendingUnmount === modelData.path && root.pendingVerb === "unmount")) return 96;
                                        if (root.storage.busyAction) return 92;
                                        return 76;
                                    }
                                    Layout.preferredHeight: 30
                                    radius: 15
                                    color: {
                                        if ((root.pendingUnmount === modelData.path && root.pendingVerb === "unmount"))
                                            return Theme.error;
                                        if (root.storage.busyAction)
                                            return Theme.surfaceContainerHigh;
                                        if (!modelData.mounted)
                                            return actionButtonMouse.containsMouse ? Theme.primaryHover : Theme.primary;
                                        return actionButtonMouse.containsMouse ? Theme.errorHover : Theme.surfaceContainerHigh;
                                    }
                                    Behavior on Layout.preferredWidth { NumberAnimation { duration: 120 } }

                                    StyledText {
                                        anchors.centerIn: parent
                                        visible: !root.storage.busyAction && (root.pendingUnmount !== modelData.path || root.pendingVerb !== "unmount") && modelData.mounted
                                        text: "Unmount"
                                        font.pixelSize: Theme.fontSizeSmall
                                        font.weight: Font.Bold
                                        color: actionButtonMouse.containsMouse ? Theme.error : Theme.surfaceVariantText
                                    }

                                    StyledText {
                                        anchors.centerIn: parent
                                        visible: !root.storage.busyAction && (root.pendingUnmount === modelData.path && root.pendingVerb === "unmount")
                                        text: "Confirm? " + root.confirmCountdown
                                        font.pixelSize: Theme.fontSizeSmall
                                        font.weight: Font.Bold
                                        color: Theme.errorText
                                    }

                                    StyledText {
                                        anchors.centerIn: parent
                                        visible: root.storage.busyAction
                                        text: "Working…"
                                        font.pixelSize: Theme.fontSizeSmall
                                        font.weight: Font.Bold
                                        color: Theme.surfaceVariantText
                                    }

                                    StyledText {
                                        anchors.centerIn: parent
                                        visible: !root.storage.busyAction && (root.pendingUnmount !== modelData.path || root.pendingVerb !== "unmount") && !modelData.mounted
                                        text: modelData.locked ? "Unlock" : "Mount"
                                        font.pixelSize: Theme.fontSizeSmall
                                        font.weight: Font.Bold
                                        color: Theme.primaryText
                                    }

                                    MouseArea {
                                        id: actionButtonMouse
                                        anchors.fill: parent
                                        enabled: !root.storage.busyAction && !root.storage.probing
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: root.handleAction(modelData)
                                    }
                                }
                            }

                            Row {
                                spacing: Theme.spacingS
                                DButton {
                                    visible: modelData.canLock && root.storage.actionsAvailable
                                    text: root.pendingUnmount === modelData.path && root.pendingVerb === "lock" ? "Confirm lock? " + root.confirmCountdown : "Unmount and lock"
                                    enabled: !root.storage.busyAction && !root.storage.probing
                                    onClicked: root.handleAction(modelData, "lock")
                                }
                                StyledText {
                                    text: modelData.encrypted ? (modelData.locked ? "Encrypted · locked" : "Encrypted · unlocked") : ""
                                    font.pixelSize: Theme.fontSizeSmall
                                    color: Theme.surfaceVariantText
                                }
                            }

                            // Progress bar
                            RowLayout {
                                width: parent.width
                                spacing: Theme.spacingM

                                Rectangle {
                                    Layout.fillWidth: true
                                    height: 6
                                    radius: 3
                                    color: Theme.surfaceContainerHigh

                                    Rectangle {
                                        width: Math.max(0, Math.min(parent.width, parent.width * modelData.usePercent / 100))
                                        height: parent.height
                                        radius: 3
                                        color: root.partitionColor(modelData)
                                    }
                                }

                                StyledText {
                                    text: root.usageText(modelData)
                                    font.pixelSize: Theme.fontSizeSmall
                                    color: Theme.surfaceText
                                }
                            }
                        }
                    }
                }

                StyledText {
                    width: parent.width
                    visible: root.storage.visiblePartitions.length === 0 && !root.storage.probing
                    text: "No storage partitions found"
                    font.pixelSize: Theme.fontSizeSmall
                    color: Theme.surfaceVariantText
                }

                Item {
                    width: parent.width
                    height: 24

                    StyledText {
                        anchors.fill: parent
                        text: root.storage.actionsAvailable ? "Storage actions use system authorization (udisks2)" : "Monitoring only · storage actions need Python Gio"
                        wrapMode: Text.WordWrap
                        font.pixelSize: 10
                        color: Theme.surfaceVariantText
                        horizontalAlignment: Text.AlignHCenter
                    }
                }
            }
        }
    }
}
