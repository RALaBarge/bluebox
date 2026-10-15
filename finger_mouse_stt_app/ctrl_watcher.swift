// Global Control-state watcher (Swift + CGEventTap). Detects the Control
// modifier key across ALL apps regardless of focus; stream "1"/"0" on stdout.
// Requires Accessibility / Input Monitoring permission. Run as a helper.
//
// Build: swiftc -O ctrl_watcher.swift -o ctrl_watcher

import ApplicationServices
import Foundation

var last = -1

@objc
class TapHandler {
    let proxyRef: CFMachPort
    init(proxy: CFMachPort) { proxyRef = proxy }
}

func cbProxy(_: CGEventTapProxy, type: CGEventType, event: CGEvent, _: Any?) -> CGEvent {
    if type == .keyDown || type == .keyUp || type == .flagsChanged {
        let ctrl = event.flags.rawValue & NX_CONTROLMASK != 0 ? 1 : 0
        if ctrl != lastPrinted {
            lastPrinted = ctrl
            print(ctrl)
            fflush(stdout)
        }
    }
    return event
}

func main() -> Int32 {
    let mask = CGEventMask(1 << Int(CGEventType.keyDown.rawValue)) |
               CGEventMask(1 << Int(CGEventType.keyUp.rawValue)) |
               CGEventMask(1 << Int(CGEventType.flagsChanged.rawValue))
    let tap = CGEventTapCreate(kCGHIDEventTap, kCGHeadInsertEventTap,
                               kCGEventTapOptionListenOnly, mask,
                               cbTap, nil)
    if tap == nil {
        FileHandle.standardError.write("tap create failed — grant Accessibility/Input Monitoring\n")
        return 1
    }
    let src = CGEventTapCreateRunLoopSource(tap, 0)
    CFRunLoopAddSource(CFRunLoopGetCurrent(), src, kCFRunLoopDefaultOrder)
    CFRunLoopRun()
    return 0
}

main()