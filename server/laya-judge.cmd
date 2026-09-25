@echo off
rem laya-judge launcher: runs the thin MCP bridge with the py launcher.
rem The bridge starts the detached model sidecar when no instance answers.
py -3 "%~dp0bridge.py" %*
