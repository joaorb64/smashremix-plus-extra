@echo off
setlocal
set py=python -m

where python >nul 2>&1
if errorlevel 1 (
    echo Python is not installed. Please install Python and try again.
    @echo %cmdcmdline%|find /i """%~f0""">nul && pause
    exit /b 1
)

%py% pipenv >nul 2>&1
if errorlevel 1 (
    echo Pipenv is not installed. Installing Pipenv...
    %py% pip install pipenv
    if errorlevel 1 (
        echo Failed to install Pipenv. Please install it manually and try again.
        @echo %cmdcmdline%|find /i """%~f0""">nul && pause
        exit /b 1
    )
)

echo Updating pipenv...
%py% pip install --upgrade pipenv

echo Installing dependencies from Pipfile...
%py% pipenv install

echo Running the Python script...
%py% pipenv run python character_appender.py %*
if errorlevel 1 (
    echo Character appender exited due to an error. Check above messages for more information.
    @echo %cmdcmdline%|find /i """%~f0""">nul && pause
    exit /b 1
)

echo Running the patch...
call patch_extra.bat
if errorlevel 1 (
    echo(
    echo Patching Remix failed. Check output.log for in-depth information.
    echo(
    echo Last 10 lines of output.log:
    call scripts/tail.bat output.log 10
    @echo %cmdcmdline%|find /i """%~f0""">nul && pause
    exit /b 1
)

echo Generating RDB (PJ64KSE)...
%py% pipenv run python scripts/gen_rdb.py > rdb.txt

echo Generating INI (RMG-K)...
%py% pipenv run python scripts/gen_ini.py > ini.txt