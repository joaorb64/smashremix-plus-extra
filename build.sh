#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "$SCRIPT_DIR/character_appender.py" "$@"

# A native bass is used when there is one ($BASS, the one bundled in tools/linux-x86_64/ or bass in PATH);
# otherwise the bundled bass.exe runs through wine (patch_extra.bat).
BASS="${BASS:-}"
if [ -z "$BASS" ] && [ "$(uname -s)" = "Linux" ]; then
    for candidate in "$SCRIPT_DIR/tools/linux-x86_64/bass" "$(command -v bass || true)"; do
        if [ -n "$candidate" ] && [ -f "$candidate" ] && [ -x "$candidate" ] && { [ "$(uname -m)" = "x86_64" ] || [ "$candidate" != "$SCRIPT_DIR/tools/linux-x86_64/bass" ]; }; then
            BASS="$candidate"
            break
        fi
    done
fi

if [ -z "$BASS" ]; then
    wine "$SCRIPT_DIR/patch_extra.bat"
    exit $?
fi

ROM=ssb64asm_extra.z64
LOG=output.log
ASM="$SCRIPT_DIR/smashremix/assembler"

: > "$ROM"
echo "Building \"$ROM\" with $BASS..."
if ! "$BASS" -o "$ROM" main.asm -sym logfile.log > "$LOG" 2>&1; then
    echo "BUILD FAILED"
    echo
    echo "==== Last 10 lines of $LOG ===="
    tail -n 10 "$LOG"
    exit 1
fi
echo "BUILD SUCCESS"

# The CRC update is a separate (Windows) tool
if command -v wine > /dev/null 2>&1; then
    if ! wine "$ASM/rn64crc.exe" -u "$ROM" >> "$LOG" 2>&1; then
        echo "CRC UPDATE FAILED"
        cat "$LOG"
        exit 1
    fi
else
    echo "wine not found: the ROM's CRC was not updated (rn64crc.exe)" >&2
fi

echo "Build log exported to \"$LOG\""
