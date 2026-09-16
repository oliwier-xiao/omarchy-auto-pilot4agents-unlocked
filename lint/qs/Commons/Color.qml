pragma Singleton
import QtQuick

// Lint and offscreen-test stand-in for omarchy-shell's qs.Commons Color singleton.
// Hand-written: the colour names of the real singleton with its built-in fallback
// palette. The real one reads the current theme files; a stand-in never touches disk.
QtObject {
  id: root

  component BarColors: QtObject {
    property color background: "#101315"
    property color text: "#cacccc"
    property color active: "#a55555"
  }

  component SurfaceColors: QtObject {
    property color background: "#101315"
    property color text: "#cacccc"
    property color border: "#cacccc"
  }

  readonly property string home: ""

  property color foreground: "#cacccc"
  property color background: "#101315"
  property color accent: "#cacccc"
  property color urgent: "#a55555"
  property color muted: "#707880"

  readonly property BarColors bar: BarColors {
    background: root.background
    text: root.foreground
    active: root.urgent
  }

  readonly property SurfaceColors popups: SurfaceColors {
    background: root.background
    text: root.foreground
    border: root.accent
  }

  readonly property SurfaceColors tooltip: SurfaceColors {
    background: root.background
    text: root.foreground
    border: root.foreground
  }

  readonly property SurfaceColors notifications: SurfaceColors {
    background: root.background
    text: root.foreground
    border: root.accent
  }

  readonly property SurfaceColors menu: SurfaceColors {
    background: root.background
    text: root.foreground
    border: root.foreground
  }
}
