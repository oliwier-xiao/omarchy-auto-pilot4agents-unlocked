# Lint imports

`qmllint` cannot see the shell this plugin runs inside. `qs.Commons` and `qs.Ui` come from
omarchy-shell and `Quickshell.*` from the compositor toolkit, and neither is on a linter's import
path. Without them every shell type is an unresolved import and the real diagnostics drown. This
folder supplies that import context:

```
/usr/lib/qt6/bin/qmllint -I lint Service.qml
```

Nothing here is loaded by the plugin. `dev-sync.sh` leaves the folder out of the live copy.

## What is in it

Every file is a hand-written stand-in, not a copy of upstream code. Each one declares the public
members of the real type with the same names, types and defaults, and nothing else.

- `qs/Commons/`: `Style`, `Color`, `Util` and `Border` singletons. The token groups
  (`Style.font`, `Style.bar`, `Style.spacing`, `Color.popups`, ...) are typed inline components,
  so a lint run with `--ignore-settings` still checks every token name the plugin reads.
- `qs/Ui/`: `BarWidget`, `Panel`, `PanelController`, `KeyboardPanel`, `WidgetButton`,
  `OpticalGlyph`, `BorderSurface`, `CursorSurface`, `PanelKeyCatcher`, `PanelSeparator`.
  `KeyboardPanel` is a plain `Item` here (upstream it is a layer-shell window), which lets
  `tests/qml.test.sh` instantiate the whole panel offscreen.
- `Quickshell/` and `Quickshell/Io/`: the `Quickshell` singleton (`env`, `clipboardText`),
  `IpcHandler`, `Process`, `SplitParser` and `StdioCollector`.

`bar` is `var` in the stand-ins where upstream declares a `QtObject`, because the host bar's
members are not declared anywhere a linter could read them.

The shell helpers that start detached processes are left out of `Util` and `Quickshell` on purpose.
The plugin must never call them, and a call to a member that does not exist fails the lint run.

## Two environments

On a machine with Quickshell installed, the system `Quickshell` modules may be picked up before
these stand-ins. On a bare runner only the stand-ins resolve. Both must lint clean, and both have
been checked: `tests/qml.test.sh` runs the normal mode, and a `--bare` run with only the Qt modules
on the import path resolves every import through this folder.

## Warnings

`.qmllint.ini` at the repository root sets zero warnings and switches off one category,
`missing-property`. The reason is written next to the setting. Everything else, including
unresolved types and failed imports, stays on.

## Keeping them current

When a plugin file starts using another shell type or member, add it here with the upstream name
and type, then run `tests/qml.test.sh`.
