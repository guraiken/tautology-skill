@echo off
setlocal
REM Runs a hook script with the first Python 3.10+ found on Windows. TAUTOLOGY_PYTHON overrides the search.
REM No Python: the hook does nothing (exit 0), so it never blocks an edit, a prompt or an answer.
REM The interpreter found is cached so hooks do not spawn a second probe process on every call.
if defined TAUTOLOGY_PYTHON (
  "%TAUTOLOGY_PYTHON%" %*
  goto :done
)
set "CACHE_DIR=%LOCALAPPDATA%\tautology"
if not defined LOCALAPPDATA set "CACHE_DIR=%TEMP%\tautology"
set "CACHE=%CACHE_DIR%\python.txt"
set "CACHED="
if exist "%CACHE%" set /p CACHED=<"%CACHE%"
if /i "%CACHED%"=="py -3" goto :run_cached_py
if defined CACHED if exist "%CACHED%" goto :run_cached_exe
goto :probe

:run_cached_py
py.exe -3 %*
if errorlevel 9009 if not errorlevel 9010 goto :stale
goto :done

:run_cached_exe
"%CACHED%" %*
goto :done

:stale
del /q "%CACHE%" >nul 2>nul

:probe
py.exe -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul
if errorlevel 1 goto :probe_where
if not exist "%CACHE_DIR%" mkdir "%CACHE_DIR%" >nul 2>nul
>"%CACHE%" echo py -3
py.exe -3 %*
goto :done

:probe_where
for %%C in (python3.exe python.exe) do (
  for /f "delims=" %%P in ('where %%C 2^>nul') do (
    "%%P" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul
    if not errorlevel 1 (
      set "FOUND=%%P"
      goto :save_and_run
    )
  )
)
echo tautology plugin: Python 3.10+ not found; set TAUTOLOGY_PYTHON >&2
exit /b 0

:save_and_run
if not exist "%CACHE_DIR%" mkdir "%CACHE_DIR%" >nul 2>nul
>"%CACHE%" echo %FOUND%
"%FOUND%" %*

:done
exit /b %errorlevel%
