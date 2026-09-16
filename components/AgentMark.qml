import QtQuick
import QtQuick.Shapes

// One mark per agent, each the product's own, drawn rather than shipped.
//
// The paths come from lobehub/lobe-icons (MIT), normalised to a 24x24 box and one
// even-odd path each; claude, opencode and codex are the ones agent-skills-manager
// already draws. Gemini CLI is its four-point sparkle, filled with the CLI's own
// gradient (its TUI theme), which is its second channel: its first gradient stop
// sits only a few degrees from OpenCode's blue, so hue alone could not tell the
// two apart.
//
// Vector rather than bitmap: a path takes `color` and `size` for free, and Nerd
// Fonts carries a glyph for none of these.
Item {
  id: mark

  required property var theme

  // "claude" | "opencode" | "codex" | "gemini" | "cursor" | "pi". Anything else draws
  // nothing, rather than some other tool's mark. Cursor and Pi are monochrome by their
  // makers' choice; the theme's `harnessMark` gives them their grey (Tint.MARK_BRAND).
  property string agent: ""
  property real size: 14
  property color color: mark.theme.harnessMark(mark.agent)
  // Three colours turn the fill into a gradient. Gemini gets the theme's solved
  // stops unless the caller says otherwise.
  property var gradientStops: mark.agent === "gemini" && mark.theme.geminiStops ? mark.theme.geminiStops : []
  property bool dimmed: false

  readonly property bool gradientFill: !!mark.gradientStops && mark.gradientStops.length === 3

  // Re-emitted with every argument separated. Qt's PathSvg does not apply SVG's
  // rule that an arc's two flag digits may be glued to the next number
  // (`a.848.848 0 00-1.473.842`) and reads "00" as one number, which tears every
  // arc after it. Same paths, spaced.
  readonly property var paths: ({
    claude: "M 4.709 15.955 l 4.72 -2.647 l .08 -.23 l -.08 -.128 H 9.2 l -.79 -.048 l -2.698 -.073 l -2.339 -.097 l -2.266 -.122 l -.571 -.121 L 0 11.784 l .055 -.352 l .48 -.321 l .686 .06 l 1.52 .103 l 2.278 .158 l 1.652 .097 l 2.449 .255 h .389 l .055 -.157 l -.134 -.098 l -.103 -.097 l -2.358 -1.596 l -2.552 -1.688 l -1.336 -.972 l -.724 -.491 l -.364 -.462 l -.158 -1.008 l .656 -.722 l .881 .06 l .225 .061 l .893 .686 l 1.908 1.476 l 2.491 1.833 l .365 .304 l .145 -.103 l .019 -.073 l -.164 -.274 l -1.355 -2.446 l -1.446 -2.49 l -.644 -1.032 l -.17 -.619 a 2.97 2.97 0 0 1 -.104 -.729 L 6.283 .134 L 6.696 0 l .996 .134 l .42 .364 l .62 1.414 l 1.002 2.229 l 1.555 3.03 l .456 .898 l .243 .832 l .091 .255 h .158 V 9.01 l .128 -1.706 l .237 -2.095 l .23 -2.695 l .08 -.76 l .376 -.91 l .747 -.492 l .584 .28 l .48 .685 l -.067 .444 l -.286 1.851 l -.559 2.903 l -.364 1.942 h .212 l .243 -.242 l .985 -1.306 l 1.652 -2.064 l .73 -.82 l .85 -.904 l .547 -.431 h 1.033 l .76 1.129 l -.34 1.166 l -1.064 1.347 l -.881 1.142 l -1.264 1.7 l -.79 1.36 l .073 .11 l .188 -.02 l 2.856 -.606 l 1.543 -.28 l 1.841 -.315 l .833 .388 l .091 .395 l -.328 .807 l -1.969 .486 l -2.309 .462 l -3.439 .813 l -.042 .03 l .049 .061 l 1.549 .146 l .662 .036 h 1.622 l 3.02 .225 l .79 .522 l .474 .638 l -.079 .485 l -1.215 .62 l -1.64 -.389 l -3.829 -.91 l -1.312 -.329 h -.182 v .11 l 1.093 1.068 l 2.006 1.81 l 2.509 2.33 l .127 .578 l -.322 .455 l -.34 -.049 l -2.205 -1.657 l -.851 -.747 l -1.926 -1.62 h -.128 v .17 l .444 .649 l 2.345 3.521 l .122 1.08 l -.17 .353 l -.608 .213 l -.668 -.122 l -1.374 -1.925 l -1.415 -2.167 l -1.143 -1.943 l -.14 .08 l -.674 7.254 l -.316 .37 l -.729 .28 l -.607 -.461 l -.322 -.747 l .322 -1.476 l .389 -1.924 l .315 -1.53 l .286 -1.9 l .17 -.632 l -.012 -.042 l -.14 .018 l -1.434 1.967 l -2.18 2.945 l -1.726 1.845 l -.414 .164 l -.717 -.37 l .067 -.662 l .401 -.589 l 2.388 -3.036 l 1.44 -1.882 l .93 -1.086 l -.006 -.158 h -.055 L 4.132 18.56 l -1.13 .146 l -.487 -.456 l .061 -.746 l .231 -.243 l 1.908 -1.312 l -.006 .006 z",
    opencode: "M 16 6 H 8 v 12 h 8 V 6 z m 4 16 H 4 V 2 h 16 v 20 z",
    codex: "M 8.086 .457 a 6.105 6.105 0 0 1 3.046 -.415 c 1.333 .153 2.521 .72 3.564 1.7 a .117 .117 0 0 0 .107 .029 c 1.408 -.346 2.762 -.224 4.061 .366 l .063 .03 l .154 .076 c 1.357 .703 2.33 1.77 2.918 3.198 c .278 .679 .418 1.388 .421 2.126 a 5.655 5.655 0 0 1 -.18 1.631 a .167 .167 0 0 0 .04 .155 a 5.982 5.982 0 0 1 1.578 2.891 c .385 1.901 -.01 3.615 -1.183 5.14 l -.182 .22 a 6.063 6.063 0 0 1 -2.934 1.851 a .162 .162 0 0 0 -.108 .102 c -.255 .736 -.511 1.364 -.987 1.992 c -1.199 1.582 -2.962 2.462 -4.948 2.451 c -1.583 -.008 -2.986 -.587 -4.21 -1.736 a .145 .145 0 0 0 -.14 -.032 c -.518 .167 -1.04 .191 -1.604 .185 a 5.924 5.924 0 0 1 -2.595 -.622 a 6.058 6.058 0 0 1 -2.146 -1.781 c -.203 -.269 -.404 -.522 -.551 -.821 a 7.74 7.74 0 0 1 -.495 -1.283 a 6.11 6.11 0 0 1 -.017 -3.064 a .166 .166 0 0 0 .008 -.074 a .115 .115 0 0 0 -.037 -.064 a 5.958 5.958 0 0 1 -1.38 -2.202 a 5.196 5.196 0 0 1 -.333 -1.589 a 6.915 6.915 0 0 1 .188 -2.132 c .45 -1.484 1.309 -2.648 2.577 -3.493 c .282 -.188 .55 -.334 .802 -.438 c .286 -.12 .573 -.22 .861 -.304 a .129 .129 0 0 0 .087 -.087 A 6.016 6.016 0 0 1 5.635 2.31 C 6.315 1.464 7.132 .846 8.086 .457 z m -.804 7.85 a .848 .848 0 0 0 -1.473 .842 l 1.694 2.965 l -1.688 2.848 a .849 .849 0 0 0 1.46 .864 l 1.94 -3.272 a .849 .849 0 0 0 .007 -.854 l -1.94 -3.393 z m 5.446 6.24 a .849 .849 0 0 0 0 1.695 h 4.848 a .849 .849 0 0 0 0 -1.696 h -4.848 z",
    gemini: "M 20.616 10.835 a 14.147 14.147 0 0 1 -4.45 -3.001 a 14.111 14.111 0 0 1 -3.678 -6.452 a .503 .503 0 0 0 -.975 0 a 14.134 14.134 0 0 1 -3.679 6.452 a 14.155 14.155 0 0 1 -4.45 3.001 c -.65 .28 -1.318 .505 -2.002 .678 a .502 .502 0 0 0 0 .975 c .684 .172 1.35 .397 2.002 .677 a 14.147 14.147 0 0 1 4.45 3.001 a 14.112 14.112 0 0 1 3.679 6.453 a .502 .502 0 0 0 .975 0 c .172 -.685 .397 -1.351 .677 -2.003 a 14.145 14.145 0 0 1 3.001 -4.45 a 14.113 14.113 0 0 1 6.453 -3.678 a .503 .503 0 0 0 0 -.975 a 13.245 13.245 0 0 1 -2.003 -.678 z",
    // The next two are agent-skills-manager's AgentMark.qml path strings (MIT, same
    // author): pi.dev's stepped P with its square i-dot, and Cursor's hexagon outline.
    pi: "M 1 1 h 16.5 v 11 H 12 v 5.5 H 6.5 V 23 H 1 V 1 z m 5.5 5.5 V 12 H 12 V 6.5 H 6.5 z M 17.5 12 H 23 v 11 h -5.5 V 12 z",
    cursor: "M 22.106 5.68 L 12.5 .135 a .998 .998 0 0 0 -.998 0 L 1.893 5.68 a .84 .84 0 0 0 -.419 .726 v 11.186 c 0 .3 .16 .577 .42 .727 l 9.607 5.547 a .999 .999 0 0 0 .998 0 l 9.608 -5.547 a .84 .84 0 0 0 .42 -.727 V 6.407 a .84 .84 0 0 0 -.42 -.726 z m -.603 1.176 L 12.228 22.92 c -.063 .108 -.228 .064 -.228 -.061 V 12.34 a .59 .59 0 0 0 -.295 -.51 l -9.11 -5.26 c -.107 -.062 -.063 -.228 .062 -.228 h 18.55 c .264 0 .428 .286 .296 .514 z"
  })

  readonly property string pathData: mark.paths.hasOwnProperty(mark.agent) ? mark.paths[mark.agent] : ""

  // Whole pixels: a fractional box is what makes a small, detailed path look furry.
  implicitWidth: Math.round(mark.size)
  implicitHeight: Math.round(mark.size)
  width: implicitWidth
  height: implicitHeight
  opacity: mark.dimmed ? 0.45 : 1

  // Drawn through a layer. A Shape paints outside an ancestor's clip (Qt 6.11, with the
  // default and the software backend alike), so a mark scrolled out of a list or a sheet
  // would float over the text above it; a layer is a texture, and the clip cuts that.
  layer.enabled: mark.pathData !== ""
  layer.smooth: true

  Shape {
    anchors.fill: parent
    preferredRendererType: Shape.CurveRenderer
    visible: mark.pathData !== "" && !mark.gradientFill

    transform: Scale {
      xScale: mark.width / 24
      yScale: mark.height / 24
    }

    ShapePath {
      fillColor: mark.color
      // Authored even-odd: opencode's frame is a rectangle with a rectangular hole.
      fillRule: ShapePath.OddEvenFill
      strokeColor: "transparent"
      strokeWidth: 0
      PathSvg { path: mark.pathData }
    }
  }

  // The gradient runs in path space (bottom left to top right of the 24 box); the
  // Scale transform carries it with the shape.
  Shape {
    anchors.fill: parent
    preferredRendererType: Shape.CurveRenderer
    visible: mark.pathData !== "" && mark.gradientFill

    transform: Scale {
      xScale: mark.width / 24
      yScale: mark.height / 24
    }

    ShapePath {
      fillRule: ShapePath.OddEvenFill
      strokeColor: "transparent"
      strokeWidth: 0
      fillGradient: LinearGradient {
        x1: 3; y1: 21; x2: 21; y2: 3
        GradientStop { position: 0.0; color: mark.gradientFill ? mark.gradientStops[0] : mark.color }
        GradientStop { position: 0.5; color: mark.gradientFill ? mark.gradientStops[1] : mark.color }
        GradientStop { position: 1.0; color: mark.gradientFill ? mark.gradientStops[2] : mark.color }
      }
      PathSvg { path: mark.pathData }
    }
  }
}
