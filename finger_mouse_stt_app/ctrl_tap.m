// Global Control-tap listener for macOS (compiled via swift or clang with
// the CGEvent machinery). Installs an event tap that watches Control key
// DOWN events across ALL apps (globally), and forwards "1"/"0" lines over
// stdin/stdout so a Python daemon can react. Requires Accessibility /
// Input Monitoring permission for the process.
//
// Build: clang -framework CoreFoundation -framework ApplicationServices \
//        -o ctrl_tap ctrl_tap.m  (or swiftc)
//
// Protocol: streams "1\n" while Control is held, "0\n" when released.

#import <Cocoa/Cocoa.h>
#import <ApplicationServices/ApplicationServices.h>

static CGEventRef tapCallback(CGEventTapProxy proxy, CGEventType type,
                              CGEventRef event, void *refcon) {
    // We only care about raw key events for the control modifier.
    if (type != kCGEventKeyDown && type != kCGEventKeyUp &&
        type != kCGEventFlagsChanged) return event;
    // Read the current control key state from the event flags.
    CGEventFlags flags = CGEventGetFlags(event);
    BOOL ctrl = (flags & kCGEventFlagMaskControl) != 0;
    fprintf(stderr, "%s\n", ctrl ? "1" : "0");
    fflush(stderr);
    // Pass the event through untouched.
    return event;
}

int main(int argc, char **argv) {
    // Install a passive (listen-only, do not swallow) event tap at system level.
    CGEventTapLocation loc = kCGHIDEventTap;  // system-wide, sees all keys
    CGEventTapPlacement place = kCGHeadInsertEventTap;
    CGEventTapOptions opts = kCGEventTapOptionListenOnly;
    CFMachPort mp = NULL;
    CFRunLoopSourceRef src = NULL;
    CGEventTapRef tap = CGEventTapCreate(loc, place, opts,
                                         CGEventMaskBit(kCGEventKeyDown) |
                                         CGEventMaskBit(kCGEventKeyUp) |
                                         CGEventMaskBit(kCGEventFlagsChanged),
                                         (CGEventTapCallBack)&cgEventTapCallback,
                                         NULL);
    if (!tap) { fprintf(stderr, "CGEventTapCreate failed — check Accessibility/Input Monitoring permission\n"); return 1; }
    src = CFMachPortCreateRunLoopSource(NULL, CGEventMachPort(tap), 0);
    if (!src) { fprintf(stderr, "runloop source failed\n"); return 1; }
    CFRunLoopAddSource(CFRunLoopGetCurrent(), src, kCFRunLoopDefaultOrder);
    CFRunLoopRun();
    return 0;
}