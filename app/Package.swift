// swift-tools-version: 6.0
import PackageDescription

let package = Package(
  name: "BosunMenu",
  platforms: [.macOS(.v14)],
  products: [
    .library(name: "BosunMenuCore", targets: ["BosunMenuCore"]),
    .executable(name: "BosunMenu", targets: ["BosunMenu"]),
  ],
  dependencies: [
    // Its command plugin is the linter (`swift package plugin swiftlint`); Package.resolved pins the
    // version, so laptops and CI run the same one.
    .package(url: "https://github.com/SimplyDanny/SwiftLintPlugins", from: "0.65.1")
  ],
  targets: [
    // Pure: no I/O and no AppKit. The contract, the menu, the flows and their coordinator (which acts only
    // through the protocols it is given), the dialogs' words, the poller's timing (on a clock it is given).
    // It carries the most tests and, with BosunMenuSystem, the coverage floor.
    .target(name: "BosunMenuCore"),
    // What touches the system, Foundation only: running bosun (`Process`), reading a process's start (sysctl),
    // and the files of the test hooks and of the `.command` scripts. Tested against scripts and temp folders.
    .target(name: "BosunMenuSystem", dependencies: ["BosunMenuCore"]),
    // The AppKit shell: status item, menu, alerts, workspace. Kept thin; tested for what it renders.
    .executableTarget(name: "BosunMenu", dependencies: ["BosunMenuCore", "BosunMenuSystem"]),
    .testTarget(
      name: "BosunMenuCoreTests",
      dependencies: ["BosunMenuCore"],
      resources: [.copy("Fixtures")]
    ),
    .testTarget(name: "BosunMenuSystemTests", dependencies: ["BosunMenuSystem", "BosunMenuCore"]),
    .testTarget(name: "BosunMenuTests", dependencies: ["BosunMenu", "BosunMenuCore", "BosunMenuSystem"]),
  ],
  swiftLanguageModes: [.v6]
)
