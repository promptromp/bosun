import Darwin
import Foundation

/// When a process started, in seconds since 1970, from the kernel's process table (`kp_proc.p_starttime`).
/// `NSRunningApplication.launchDate` is nil for an app launchd starts directly, which is how the login job
/// starts this one. Nil when the process is not there or the kernel will not say; never 0.
public func processStartTime(pid: Int32) -> Double? {
  var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, pid]
  var info = kinfo_proc()
  var size = MemoryLayout<kinfo_proc>.stride
  guard sysctl(&mib, UInt32(mib.count), &info, &size, nil, 0) == 0, size == MemoryLayout<kinfo_proc>.stride,
    info.kp_proc.p_pid == pid
  else { return nil }
  let started = info.kp_proc.p_starttime
  let seconds = Double(started.tv_sec) + Double(started.tv_usec) / 1_000_000
  return seconds > 0 ? seconds : nil
}
