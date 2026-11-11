# XCX Recomp mods

The mod library for [XCX Recomp](https://github.com/jmars125/xcx-recomp). The mod manager in XCX Setup reads this
repository's `index.json`, so players install, update, turn on, turn off and remove mods with a click.

Two kinds of mod are listed:

- **Lua mods** (`mods/<id>/`), hosted here: scripts that run sandboxed inside the game.
- **Game-file mods** (`links/<id>.ini`), hosted by their authors, for example on GameBanana: models, textures and
  data files that replace the game's own. This repository holds only a link, the zip's SHA-256 and credits; the
  manager downloads the zip from the author's page and checks it before unpacking. No game files and no one else's
  files are stored here.

## Playing with mods

Open XCX Setup, pick your install folder and choose **Mods**. Select a mod and choose **Install**, then turn it on or
off. Game-file mods switch while the game runs: a change applies the next time the game loads that file, so some
(data tables read at start-up) need a restart; each entry's notes say so. Your saves are never touched, but a save made
with a mod that adds missions or items can lose them when the mod is removed.

## Adding a Lua mod

1. Fork this repository and add a folder `mods/<id>/` with:
   - `mod.ini` and `main.lua`, as described in the XCX Recomp mod guides
     ([start here](https://github.com/jmars125/xcx-recomp/blob/master/docs/mods/README.md)). The folder name must
     equal the `id` in `mod.ini`.
   - `info.ini`, read only by the mod manager:
     ```ini
     description=One sentence on what the mod does.
     author=Your name
     license=MIT
     homepage=https://example.com/optional
     ```
   - Optionally `README.md` and `LICENSE`. No other files.
2. Run `python tools/build_index.py`. It checks your package the way the game will and rewrites `index.json`.
3. Open a pull request. CI runs the same check.

Raise `version` in `mod.ini` for every change, so players see the update. Lua mods run sandboxed: no file, network or
OS access. Native providers (compiled DLL/SO code) are not accepted here for now.

## Listing a game-file mod

Game-file mods use the Switch release's layout: a zip with a `romfs/` folder (`romfs/chr/np/np009001.wimdo`), or
`romfs/mod/` for mods made for the XCXDE mod loader. Files in `exefs/` are Switch code and are not used.

Add `links/<id>.ini` (the file name is the id: a-z first, then a-z 0-9 . _ -):

```ini
name=Potato Tatsu
version=1
description=One sentence on what the mod does.
author=The author's name, as on their page
license=Optional
homepage=https://gamebanana.com/mods/651545
url=https://gamebanana.com/dl/1619737
sha256=SHA-256 of the zip, lowercase
size=Size of the zip in bytes
notes=Optional: what players should know, such as "restart after turning it on"
```

When the author uploads a new zip, update `url`, `sha256`, `size` and `version`. List a mod only if its author allows
it to be shared or linked, and credit them as on their page. Run `python tools/build_index.py` and open a pull
request.

## License

The tooling is MIT. Each mod's license is its author's: see `info.ini` or the mod's own page.
