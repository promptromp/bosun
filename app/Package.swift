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
    // Pure (and Foundation-only) code: the models, the flows and their coordinator, the poller and the
    // process runner. It carries the tests and the coverage floor.
    .target(name: "BosunMenuCore"),
    // The AppKit shell: status item, menu, alerts, workspace. Kept thin; tested for what it renders.
    .executableTarget(name: "BosunMenu", dependencies: ["BosunMenuCore"]),
    .testTarget(
      name: "BosunMenuCoreTests",
      dependencies: ["BosunMenuCore"],
      resources: [.copy("Fixtures")]
    ),
    .testTarget(name: "BosunMenuTests", dependencies: ["BosunMenu", "BosunMenuCore"]),
  ],
  swiftLanguageModes: [.v6]
)
