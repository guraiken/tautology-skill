@echo off
REM Runs a hook script with the first Python 3.10+ found on Windows. TAUTOLOGY_PYTHON overrides the search.
REM No Python: the hook does nothing (exit 0), so it never blocks an edit, a prompt or an answer.
if defined TAUTOLOGY_PYTHON (
  "%TAUTOLOGY_PYTHON%" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && (
    "%TAUTOLOGY_PYTHON%" %*
    exit /b %ERRORLEVEL%
  )
)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && (
  py -3 %*
  exit /b %ERRORLEVEL%
)
for %%C in (python3.exe python.exe) do (
  for /f "delims=" %%P in ('where %%C 2^>nul') do (
    "%%P" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && (
      "%%P" %*
      exit /b %ERRORLEVEL%
    )
  )
)
echo tautology plugin: Python 3.10+ not found; set TAUTOLOGY_PYTHON >&2
exit /b 0
