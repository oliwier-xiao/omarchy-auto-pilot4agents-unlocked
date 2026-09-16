pragma Singleton
import QtQuick

// Lint and offscreen-test stand-in for omarchy-shell's qs.Commons Style singleton.
// Hand-written: the token names and default values of the real singleton, and the
// pure helpers a plugin calls. The real one also follows fontconfig and Hyprland at
// run time; none of that belongs in a stand-in.
//
// The nested token groups are typed inline components here, where upstream declares
// them as plain QtObject, so qmllint can check every member a plugin reads.
QtObject {
  id: root

  component FontTokens: QtObject {
    readonly property string family: "monospace"
    readonly property string resolvedFamily: "monospace"
    readonly property string menuFamily: "monospace"
    readonly property int baseSize: 12
    readonly property int caption: 10
    readonly property int bodySmall: 11
    readonly property int body: 12
    readonly property int subtitle: 13
    readonly property int title: 14
    readonly property int heading: 16
    readonly property int display: 24
    readonly property int displayLarge: 28
    readonly property int iconSmall: 11
    readonly property int icon: 14
    readonly property int iconLarge: 18
  }

  component BarTokens: QtObject {
    readonly property int sizeHorizontal: 26
    readonly property int sizeVertical: 28
    readonly property int iconSlot: 27
    readonly property int iconCanvas: 16
    readonly property int iconFont: 13
    readonly property int statusSlot: 21
  }

  component SpacingTokens: QtObject {
    readonly property real scale: 1
    readonly property int hairline: 1
    readonly property int xxs: 2
    readonly property int xs: 3
    readonly property int sm: 4
    readonly property int md: 6
    readonly property int lg: 8
    readonly property int xl: 10
    readonly property int xxl: 12
    readonly property int xxxl: 14
    readonly property int huge: 18
    readonly property int controlGap: 8
    readonly property int controlPaddingX: 10
    readonly property int controlPaddingY: 6
    readonly property int inputPaddingY: 7
    readonly property int controlHeight: 28
    readonly property int popupRowHeight: 28
    readonly property int dropdownWidth: 240
    readonly property int searchableDropdownWidth: 260
    readonly property int numberFieldWidth: 120
    readonly property int searchablePopupMinHeight: 220
    readonly property int rowGap: 8
    readonly property int rowPaddingX: 12
    readonly property int labelGap: 4
    readonly property int panelGap: 14
    readonly property int panelPadding: 18
    readonly property int popupPadding: 14
  }

  property int cornerRadius: 0
  property int gapsOut: 5
  property real spacingScale: 1.0
  property bool spacingScaleWithFont: true
  property string fontFamily: "monospace"
  property int fontBaseSize: 12
  readonly property real fontScale: Math.max(1 / 12, fontBaseSize / 12)
  readonly property real effectiveSpacingScale: spacingScale * (spacingScaleWithFont ? fontScale : 1)

  readonly property FontTokens font: FontTokens {}
  readonly property BarTokens bar: BarTokens {}
  readonly property SpacingTokens spacing: SpacingTokens {}

  readonly property real normalFillAlpha: 0.04
  readonly property real hoverFillAlpha: 0.08
  readonly property real selectedFillAlpha: 0.18
  readonly property real pressedFillAlpha: 0.22
  readonly property real selectionFillAlpha: 0.35
  readonly property real normalBorderAlpha: 0.4
  readonly property real hoverBorderAlpha: 0.25
  readonly property real selectedBorderAlpha: 1.0

  function spaceReal(px) {
    return Number(px) * root.effectiveSpacingScale
  }

  function space(px) {
    return Math.round(root.spaceReal(px))
  }

  function fontPx(mult) {
    return Math.max(1, Math.round(root.fontBaseSize * Number(mult)))
  }

  function tinted(base, opacity) {
    var c = typeof base === "string" ? Qt.color(base) : base
    if (!c) return Qt.rgba(0, 0, 0, opacity)
    return Qt.rgba(c.r, c.g, c.b, opacity)
  }

  function normalFillFor(foreground, accent, urgent) { return root.tinted(foreground, root.normalFillAlpha) }
  function hoverFillFor(foreground, accent, urgent) { return root.tinted(foreground, root.hoverFillAlpha) }
  function selectedFillFor(foreground, accent, urgent) { return root.tinted(foreground, root.selectedFillAlpha) }
  function pressedFillFor(foreground, accent, urgent) { return root.tinted(foreground, root.pressedFillAlpha) }
  function selectionFillFor(foreground, accent, urgent) { return root.tinted(foreground, root.selectionFillAlpha) }
  function normalBorderFor(foreground, accent, urgent) { return root.tinted(foreground, root.normalBorderAlpha) }
  function hoverBorderFor(foreground, accent, urgent) { return root.tinted(foreground, root.hoverBorderAlpha) }
  function selectedBorderFor(foreground, accent, urgent) { return root.tinted(foreground, root.selectedBorderAlpha) }
}
