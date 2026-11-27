<p align="center">
 <img width="320" alt="Smash Remix +EXTRA" src=".github/sr-extra.png">
</p>

# Extra content build system
- **NOTE: This is not an official update to "Smash Remix" This is only a tool to add fan content to the game. The patches we provide are a community effort of new content all in one patch.**  
- If you would like to download our newest patch, please go here: [Latest Release](https://github.com/joaorb64/smashremix-plus-extra/releases)
  - After you have downloaded our patch, you can use [this patcher](https://kotcrab.github.io/xdelta-wasm/) to create the rom. 

# How To Play
- If you are on MOBILE, you need an emulator that will properly run ROMs over 64MB.  
For Android, best emulator would be [Mupen64Plus-AE NIGHTLY BUILD](https://github.com/mupen64plus-ae/mupen64plus-ae)
- For PC (Windows, Linux) it's recommended to use [RMG-K](https://github.com/Jay-Day/RMG-K/releases/latest) as this emulator supports online play AND rollback.

## Prerequisites For Building Your Own Build
- Windows: [Python 3.12+](https://www.python.org/downloads/latest/pymanager), pipenv
  - pipenv gets installed automatically by build.bat - to install manually, run `python -m pip install pipenv` in a terminal
- Linux: Python 3.12+, pipenv, xdelta3 (wine is only needed on non-x86-64 hosts without a native `bass`)

## Setting Up Your Own Build
- Clone the repository using `git clone --recursive` (note the recursive here for it to clone the original smashremix repo as a submodule)
  - If you cloned the repo non-recursively, run `git submodule update --init --recursive` in the clone directory to initialize and update the smashremix submodule
  - If you don't have Git or downloaded this repository without it (i.e. Code -> Download ZIP), download the [Smash Remix source](https://github.com/JSsixtyfour/smashremix/archive/refs/heads/master.zip) manually and extract it's files into `smashremix`
  - Clone into a local drive location and not somewhere like OneDrive to avoid potential issues
- Copy your legal vanilla SSB64 ROM into `smashremix/roms/` as `ssb.rom`. If you have a `.z64`, just rename the extension
- Set up the Python environment: In your clone directory, run `python -m pipenv install` (automatically handled by build.bat)

## Building
**To build, simply run:**
- **Windows: `build.bat`**
  - Manual build: `python -m pipenv run character_appender.py && patch_extra.bat`
- **Linux:**
  - `pipenv run bash build.sh` (native `bass` from `tools/linux-x86_64/`, CRC via `scripts/n64crc.py`)

The build consists of two parts:
- Running `character_appender.py` which does all the magic and generates a `smashremix/roms/original_extra.z64`. This is an extension of the `/roms/original.z64` file with extra content. It also prepares the `src/` and `build/` directories
- Running `patch_extra.bat` which builds the final ROM just like Remix.

## Modding Resources
- [Modding Wiki](https://joaorb64.github.io/smash64-modding-wiki/)
- [Moveset Editor](https://github.com/joaorb64/ssb64-moveset-file-editor)
- [Scripts](https://github.com/joaorb64/smashremix-plus-extra/tree/main/scripts) for extracting characters/stages from ROM, fixing normals from Blender, projectile creation, etc. are included with +EXTRA's source.
- [Templates](https://github.com/joaorb64/smashremix-plus-extra/tree/main/templates) for various assets are included with +EXTRA's source.  

## FAQ
- **"Where do I download this MOD?"**  
Head over to the 'Releases' section and download the latest version.  
- **"Is this a new Smash Remix update?"**  
This mod is NOT an official Smash Remix update, it builds off Remix, and adds cool stuff from the community!  
- **"What is the purpose of Smash Remix +EXTRA?"**  
We wanted to design a project that will allow users to add their own custom content on top of Smash Remix. We added content across the community into +EXTRA as an example, and even added new additions. Smash Remix +EXTRA is packaged with tools to assist and ease that process of importing your own work into the game.  
- **"Will this work on the original N64?"**  
Because the filesize is over 64MB, most flash carts will not run it. As long as your flashcart is compatible with larger rom sizes, it can work, but no guarantee.  
- **"I found a crash?"**  
Please create an issue on GitHub and we will try to fix it!  
- **"Can you add >Insert Character Here<"**  
No, but now YOU can! This mod is designed to help anybody bring in whatever character, stage, song & additional content they want into the game. We even have 3D Game & Watch!  
- **"How do I add something to this mod?"**  
You can download the source code of this mod and use the additional characters and stages as references!  
- **"Will there be future updates?"**  
We will focus on some bug fixes, polish, and pesky crashes, but the project is designed to encourage people to add their own content within the game.  
- **"Where can I go to ask more questions / for extra help?"**  
You can head over to the [Smash Remix Discord Server](https://discord.gg/ChpN332), we can help there.  
