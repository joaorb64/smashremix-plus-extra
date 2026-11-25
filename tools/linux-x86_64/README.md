# bass (Linux x86-64)

Native build of the `bass` assembler used by the Smash Remix build, so Linux doesn't need wine for it
(the Windows build uses `smashremix/assembler/bass.exe`).

- Source: https://github.com/joaorb64/bass (fork of https://github.com/jtl3d/bass, which adds the `-create` / `export` features Remix needs),
  commit `205834e` ("add case-insensitive file resolution on Linux").
- Build: `git clone https://github.com/joaorb64/bass && cd bass/bass && make` (statically linked, x86-64).
- `build.sh` uses `$BASS` if set, then this binary, then `bass` in PATH; with none of them it falls back to `wine patch_extra.bat`.
