# reacalcul

**Same fridge. New answer.** reacalcul is a personal recall watch that remembers a saved appliance label and explains exactly why a later notice changes its coverage.

[Try the public Fly Sprite](https://reacalcul-b623b.sprites.app) · [Deployment runs](https://github.com/Agozie180/reacalcul/actions/workflows/deploy.yml)

## Judge walkthrough

1. Select **See what changed**. The agent replays both official notices against the sample label **EFMIS121 / A2210**. It became newly covered in January.
2. Compare **A2401** on the same model. It falls outside the January range. The receipt highlights the exact CPSC rule, both publication dates, and the official refund instructions.
3. To see persistence between notices, use the form to save a label and check July. Reload, then replay January without entering it again.

The notices, product photo, and approximately **330,000 additional units** are real. The two serial values are illustrative. The one-click sample leaves your saved label untouched.

[July CPSC notice](https://www.cpsc.gov/Recalls/2025/Curtis-International-Recalls-Frigidaire-brand-Minifridges-Due-to-Fire-and-Burn-Hazards-More-Than-700-000-Reported-in-Property-Damage) · [January expansion](https://www.cpsc.gov/Recalls/2026/Curtis-International-Expands-Recall-of-Frigidaire-brand-Minifridges-Due-to-Fire-and-Burn-Hazards)

## What runs

- **Saved label and replay.** SQLite stores each browser's appliance and the notice it has already seen. Replaying twice does not record a second change.
- **Source-backed evidence.** A Sprites Custom API connector reads the official CPSC feed. The service uses exact model and serial rules for the two historical notices and uses the official text in the decision receipt.
- **Daily watch.** GitHub Actions wakes the Sprite once a day for a read-only refresh. The Sprite compares each scan with its previous snapshot and records new notices and edits under existing recall numbers. For a saved label, it shows relevant changes detected since the label was saved, including the old and new listed serial ranges when available. A user can also check on demand. New notice formats are referred to the official source for review.
- **Low-cost hosting.** The Sprite can sleep between requests. The app uses the public CPSC data and has no model API charges. The historical replay is available from its verified snapshot when the feed is unavailable.

The watch covers this U.S. Curtis International Frigidaire recall series. Checks and updates are shown in the browser. The January expansion page links to the original notice dated July 24, 2025, even though its unit-count note says July 2024.

## Run locally

Use Python 3.10+ and run `python3 app.py`. Open `http://localhost:8080`. The repository includes a snapshot of the two public notices for the local replay.

## GitHub-to-Fly deployment

The deploy workflow runs on pushes to `main` and manual dispatch. Configure these repository settings:

- `SPRITES_TOKEN` secret for a Sprites organization token.
- `WATCH_REFRESH_KEY` secret for a randomly generated 32-byte URL-safe key shared with the scheduled workflow.
- `CPSC_CONNECTOR_URL` variable for the Sprites Custom API gateway base URL. Point the connector to `https://www.saferproducts.gov` with `/RestWebServices/Recall` allowed for the `reacalcul` Sprite.

GitHub Actions imports verified notice data on the Sprite before activating the release. It then checks the exact public commit, both serial outcomes, replay deduplication, browser session isolation, and scheduled feed authorization. Storage for saved labels and the last feed scan lives outside the release directory.
