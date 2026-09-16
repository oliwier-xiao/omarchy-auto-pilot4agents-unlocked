import QtQuick
import qs.Commons

// Lint and offscreen-test stand-in for qs.Ui CursorSurface: the shared hover-cursor chrome.
// Items derive their highlight from hasCursor/current, never from containsMouse.
BorderSurface {
  id: root

  property bool hasCursor: false
  property bool current: false
  property bool outline: false
  property bool bordered: false

  property color foreground: Color.foreground
  property color accent: Color.accent
  property color fill: Style.hoverFillFor(foreground, accent)
  property color currentFill: Style.selectedFillFor(foreground, accent)

  radius: Style.cornerRadius
  color: hasCursor ? fill : (current ? currentFill : "transparent")
  borderSpec: hasCursor
    ? Border.controlSpec("hover-cursor", foreground, accent)
    : (current ? Border.controlSpec("selected", foreground, accent)
      : (bordered ? Border.controlSpec("normal", foreground, accent) : Border.none()))
}
