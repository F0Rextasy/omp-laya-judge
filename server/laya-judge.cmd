@echo off
rem laya-judge launcher: runs the MCP server with the py launcher.
rem A .cmd wrapper (not a bare python.exe) so omp's Windows stdio path
rem (cmd.exe escaping, BatBadBut-safe) handles process creation.
py -3 "%~dp0server.py" %*
