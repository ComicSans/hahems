# Releasing & Icon

## Neue Version veröffentlichen (HACS)

HACS installiert HEMS **ordner-basiert** aus `custom_components/hems` — ein
GitHub-Release genügt, ein Zip-Asset ist nicht nötig. Wichtig ist nur, dass der
Release-Tag und die Version in `manifest.json` übereinstimmen; der Workflow
`.github/workflows/release.yml` erzwingt das.

Ablauf für eine neue Version (Beispiel `1.1.0`):

```bash
# 1. Version in manifest.json setzen (SemVer, ohne führendes v)
#    "version": "1.1.0"
# 2. Commit
git add custom_components/hems/manifest.json
git commit -m "Release 1.1.0"
# 3. Tag mit führendem v + pushen
git tag v1.1.0
git push origin main --tags
# 4. Auf GitHub ein Release aus dem Tag v1.1.0 erstellen (oder via gh):
gh release create v1.1.0 --title "1.1.0" --generate-notes
```

Der `Validate`-Workflow (hassfest + HACS-Action) läuft bei jedem Push/PR und
prüft Manifest und Repo-Struktur.

## Icon (Home-Assistant-UI)

Die Quelldatei ist [`assets/icon.svg`](assets/icon.svg); daraus abgeleitet:

- `assets/icon.png` — 256×256, randlos getrimmt
- `assets/icon@2x.png` — 512×512

Die PNGs liegen zusätzlich unter
[`custom_components/hems/brand/`](custom_components/hems/brand/) —
HACS nutzt diesen lokalen Ort als Brand-Fallback, sodass das Icon **ohne**
externen PR sofort in HACS erscheint:

```
custom_components/hems/brand/icon.png      (256×256)
custom_components/hems/brand/icon@2x.png   (512×512)
```

Damit das Icon auch außerhalb von HACS (native Integrationsseite von Home
Assistant) erscheint, gehören die PNGs zusätzlich ins offizielle
[home-assistant/brands](https://github.com/home-assistant/brands)-Repo:
`home-assistant/brands` forken, die beiden PNGs nach
`custom_integrations/hems/icon.png` bzw. `…/icon@2x.png` legen und einen PR
öffnen. Nach dem Merge lädt Home Assistant das Icon über die Domain `hems`.

### Icon neu rendern

Das SVG nutzt einen Verlauf, den ImageMagicks eingebauter SVG-Renderer nicht
korrekt auflöst. Entweder mit einem echten SVG-Renderer (`rsvg-convert`,
`inkscape`, `cairosvg`) rendern — oder aus dem bestehenden PNG den Rand per
Pixel-Trim entfernen:

```bash
magick assets/icon.png    -trim +repage -resize 256x256\! assets/icon.png
magick assets/icon@2x.png -trim +repage -resize 512x512\! assets/icon@2x.png
```
