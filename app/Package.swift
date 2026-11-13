// swift-tools-version: 6.0
import PackageDescription

let package = Package(
  name: "BosunMenu",
  platforms: [.macOS(.v14)],
  products: [
    .library(name: "BosunMenuCore", targets: ["BosunMenuCore"])
  ],
  dependencies: [
    // Its command plugin is the linter (`swift package plugin swiftlint`); Package.resolved pins the
    // version, so laptops and CI run the same one.
    .package(url: "https://github.com/SimplyDanny/SwiftLintPlugins", from: "0.65.1")
  ],
  targets: [
    .target(name: "BosunMenuCore"),
    .testTarget(
      name: "BosunMenuCoreTests",
      dependencies: ["BosunMenuCore"],
      resources: [.copy("Fixtures")]
    ),
  ],
  swiftLanguageModes: [.v6]
)
