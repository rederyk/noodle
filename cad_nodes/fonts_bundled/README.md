# Bundled fonts

Fonts shipped **with the app** so built-in example graphs render correctly on a
fresh install — even with no user-uploaded fonts and no OS-installed families
(the container has none). They are read-only: the `/api/fonts` upload/delete
endpoints only touch the user's `projects/_fonts/` library. `fonts.resolve_font`
searches the user library first, then this directory, so a user upload of the
same family still overrides the bundled copy.

| font | family | used by | license |
|------|--------|---------|---------|
| `exo2-variablefont-wght.ttf` | Exo 2 | `examples/lego-brick.json` (embossed "NOODLE" text) | SIL OFL 1.1 — see [`exo2-OFL.txt`](exo2-OFL.txt) |

**Exo 2** © 2013 The Exo 2 Project Authors (https://github.com/googlefonts/Exo-2.0).
The SIL Open Font License 1.1 explicitly permits bundling and redistribution
alongside software; the full license text is included in `exo2-OFL.txt` as the
license requires.
