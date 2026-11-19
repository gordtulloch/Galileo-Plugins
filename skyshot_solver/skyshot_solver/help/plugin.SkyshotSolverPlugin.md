# Skyshot

Adds **Skyshot**, a fast *blind* plate solver, as an extra solver. Blind means it works out where the camera points from the stars alone, with no position, scale or rotation hint, so it also recovers when the mount's position is badly wrong. It runs entirely on this computer.

Two things are needed: the **skyshot program** and a **star index**. This page downloads both. The program is an unmodified, prebuilt copy of Skyshot (Apache-2.0) made by Galileo's build for Windows, Linux (Intel/AMD and ARM, including Raspberry Pi 5) and macOS on Apple silicon. If there is none for your computer, or none has been published yet, **Download Skyshot program** is greyed out: build it once from [github.com/ashtree74/skyshot-solver](https://github.com/ashtree74/skyshot-solver) (`cargo build --release --locked`) and point **Skyshot program** at it. Once the status line says **Ready**, choose *Skyshot* under **Options > Solve > Solver**, or on the Solve screen.

Because Skyshot takes no hint, the *Search radius* and *Field-of-view hint* options under Options > Solve do not apply to it.

## Skyshot program

The skyshot executable (`skyshot.exe` on Windows). Leave blank to use the program downloaded with **Download Skyshot program**, else one found on your PATH or in `~/.cargo/bin`, where `cargo install` puts it. A path set here always wins, so use it for a build of your own.

## Index download folder

Where downloaded indexes are kept. Leave blank to use a `skyshot` folder inside Galileo's data folder. Allow a few gigabytes; the Deep index alone is about 4.3 GB once unpacked.

## Index folder

Use an index you already have, instead of the downloaded one: the folder you would pass to `skyshot --index`. Leave blank to use the downloaded Full index.

## Solve mode

Passed to Skyshot's own `--mode` option. *Auto* leaves the choice to Skyshot. *Deep* uses the Deep index, which suits narrow fields (long focal lengths) and needs the Deep download below. *Standard* asks for the ordinary search.

## Solve time limit

How long Skyshot may search before giving up, in seconds. Starting the program and loading the index are not counted, so a solve can take somewhat longer than this in total.

## Save

Stores the settings on this page. Nothing here takes effect until you save.

## Download Skyshot program

Downloads the prebuilt Skyshot program for this computer (a few megabytes) into the download folder and checks that it starts. The download is checked against a checksum built into this plugin, not one read from the download site, so a tampered file is refused. It is an unmodified build, kept with Skyshot's licence and notices, and it is **not code-signed**: it runs on your computer like any program you install. It is greyed out when no build is published for your computer, and does nothing if the program is already downloaded.

## Download Full index

Downloads and installs Skyshot's recommended index (about 0.84 GB to download). Galileo first fetches the release list and shows you the exact size and the disk space needed before anything is downloaded. Every file is checked against its published checksum, and nothing is installed if it does not match. An index already installed is never changed, so it is safe to press again.

## Download Deep index

Adds the Deep index for narrow fields (about 4.1 GB to download) next to the Full one, downloading the Full index first if it is not installed. Only needed for the *Deep* solve mode, or *Auto* on narrow fields.

## Cancel download

Stops the download. What has been downloaded is kept, and pressing the download button again carries on from there.

## Check index

Runs `skyshot inspect` on the index in use and shows what it reports, to confirm the program can read it. Needs both the program and an index.

## Credits and licences

**Skyshot Solver** is Copyright 2026 Adam Jesionkiewicz and is licensed under the Apache License 2.0 ([github.com/ashtree74/skyshot-solver](https://github.com/ashtree74/skyshot-solver)). This plugin contains none of its code. **Download Skyshot program** fetches an unmodified build made by Galileo's build workflow, which keeps Skyshot's `LICENSE`, `NOTICE` and third-party notices beside the program, and Galileo runs it as a separate program. This plugin is not part of Skyshot and is not endorsed by its author. To cite Skyshot in your own work, use its `CITATION.cff` and name the source revision and data bundle you used.

The **star indexes** are a separate download from Skyshot's own site, with terms of their own that their author describes as intended for non-commercial use. They are derived from the ASTAP D80 star catalogue ([hnsky.org/astap.htm](https://www.hnsky.org/astap.htm)), which is built from ESA's Gaia DR3 (Gaia data: ESA/Gaia/DPAC; [credit requirements](https://www.cosmos.esa.int/web/gaia/dr3)). This plugin does not redistribute them. Each installed index keeps its `LICENSE-DATA.txt` and `PROVENANCE.json` in its folder, and an index without a licence file is not installed. Please read them before using the index.

This plugin's own code is licensed GPL-3.0-or-later like the rest of Galileo.
