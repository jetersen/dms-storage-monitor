import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../StorageMonitorPopout.qml', import.meta.url), 'utf8');
const calls = [];
const root = {
    pendingPowerOff: '', pendingPowerOffIdentity: '',
    storage: {actionsAvailable: true, busyAction: false, probing: false, showUnmounted: true,
        powerOffDrive: partition => calls.push(partition.identity)},
};
const timer = {restart() {}, stop() {}};
function method(name) {
    const body = source.match(new RegExp(`    function ${name}\\([\\s\\S]*?\\n    }`))[0];
    return new Function('root', 'powerOffConfirmTimer', `${body}; return ${name};`)(root, timer);
}
const representative = method('isPowerOffRepresentative');
const confirm = method('handlePowerOff');
const partition = (name, group, allowed = true) => ({path: name, identity: [name], physicalGroup: group,
    drivePath: group, powerOffIdentity: {generation: 1}, canPowerOff: allowed, mounted: true});
const a = partition('a', 'usb'), b = partition('b', 'usb'), c = partition('c', 'other-usb');
root.storage.visiblePartitions = [a, b, c, partition('root', 'os', false)];
assert.deepEqual(root.storage.visiblePartitions.map(representative), [true, false, true, false]);
a.mounted = false;
root.storage.showUnmounted = false;
assert.equal(representative(a), false);
assert.equal(representative(b), true);
root.storage.actionsAvailable = false;
assert.equal(representative(b), false);
confirm(b);
assert.equal(calls.length, 0);
root.storage.actionsAvailable = true;
confirm(b);
assert.equal(calls.length, 0);
b.powerOffIdentity = {generation: 2};
confirm(b); // A replacement needs a new confirmation.
assert.equal(calls.length, 0);
confirm(b);
assert.deepEqual(calls, [['b']]);
for (const flag of ['busyAction', 'probing']) {
    root.storage[flag] = true;
    confirm(c); confirm(c);
    root.storage[flag] = false;
}
confirm(root.storage.visiblePartitions[3]);
assert.equal(calls.length, 1);
console.log('Power-off UI: one button per device, filtered rows and guarded confirmation passed');
