import QtQuick
import qs.Commons

// Lint and offscreen-test stand-in for qs.Ui BarWidget, the base of every bar widget.
// `bar` is `var` here (upstream: QtObject) because the host bar's members are not
// declared anywhere qmllint could read them.
Item {
  id: root

  property var bar: null
  property string moduleName: ""
  property var settings: ({})

  readonly property bool vertical: bar ? bar.vertical === true : false
  readonly property int barSize: bar && bar.barSize ? bar.barSize : Style.bar.sizeHorizontal

  function broadcast(method) {
    var items = bar && typeof bar.moduleWidgets === "function" ? bar.moduleWidgets(moduleName) : [root]
    for (var i = 0; i < items.length; i++) {
      if (items[i] && typeof items[i][method] === "function") items[i][method]()
    }
  }

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }
}
