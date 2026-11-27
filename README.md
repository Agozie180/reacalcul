# reacalcul

**Same fridge. New answer.** reacalcul is a personal recall watch that remembers your Frigidaire model and serial, checks official CPSC updates, and sends a device notification if a notice starts covering your exact label.

[Try the public Fly Sprite](https://reacalcul-b623b.sprites.app) · [Deployment runs](https://github.com/Agozie180/reacalcul/actions/workflows/deploy.yml)

## Judge walkthrough

1. Select **Replay a real recall**. On the same model, **EFMIS121 / A2210** became newly covered and **A2401** stayed outside the range.
2. Select **Send example to this device** and allow notifications. The historical example goes through the same encrypted Web Push delivery as the live watch. Open it for the quoted rule and official refund instructions.
3. To start a live watch, save a fridge label and select **Enable device alerts**. This browser stays subscribed when the tab closes. The replay leaves the live watch untouched.

The notices, product photo, and approximately **330,000 additional units** are real. The two serial values are illustrative. The one-click sample leaves your saved label untouched.

[July CPSC notice](https://www.cpsc.gov/Recalls/2025/Curtis-International-Recalls-Frigidaire-brand-Minifridges-Due-to-Fire-and-Burn-Hazards-More-Than-700-000-Reported-in-Property-Damage) · [January expansion](https://www.cpsc.gov/Recalls/2026/Curtis-International-Expands-Recall-of-Frigidaire-brand-Minifridges-Due-to-Fire-and-Burn-Hazards)

## What runs

- **Saved label and replay.** SQLite stores each browser's appliance and the notice it has already seen. Replaying twice does not record a second change.
- **Source-backed evidence.** A Sprites Custom API connector reads the official CPSC feed. The service uses exact model and serial rules for the two historical notices and uses the official text in the decision receipt.
- **Scheduled watch and opt-in device alerts.** GitHub Actions wakes the Sprite hourly. It refreshes the official feed about once a day and retries queued deliveries. An alert is queued only when a supported serial goes from outside the known ranges to inside one. The first scan establishes the baseline. Ambiguous notice formats link to the official source for review.
- **Delivery and privacy.** SQLite keeps opt-in subscriptions, the signing key, and the delivery queue on the Sprite across deployments. No account or email address is needed. A browser with Web Push is required. On iOS or iPadOS, add the site to the Home Screen and open it there.
- **Low-cost hosting.** The Sprite can sleep between requests. The app uses the public CPSC data and has no model API charges. The historical replay is available from its verified snapshot when the feed is unavailable.

The watch covers this U.S. Curtis International Frigidaire recall series. The browser also shows the last successful scan and the latest accepted alert. The January expansion page links to the original notice dated July 24, 2025, even though its unit-count note says July 2024.

## Run locally

Use Python 3.10+. Install dependencies with `python3 -m pip install -r requirements.txt`, then run `python3 app.py`. Open `http://localhost:8080`. The repository includes a snapshot of the two public notices for the local replay.

## GitHub-to-Fly deployment

The deploy workflow runs on pushes to `main` and manual dispatch. Configure these repository settings:

- `SPRITES_TOKEN` secret for a Sprites organization token.
- `WATCH_REFRESH_KEY` secret for a randomly generated 32-byte URL-safe key shared with the scheduled workflow.
- `CPSC_CONNECTOR_URL` variable for the Sprites Custom API gateway base URL. Point the connector to `https://www.saferproducts.gov` with `/RestWebServices/Recall` allowed for the `reacalcul` Sprite.

GitHub Actions imports verified notice data on the Sprite before activating the release. It then checks the exact public commit, both serial outcomes, replay deduplication, browser session isolation, and scheduled feed authorization. If verification fails after switching, the workflow restores and checks the previous release. Saved labels and the feed snapshot live outside each release directory.

## License

The project code is available under the [MIT License](LICENSE). The recall notices and product photo come from the [U.S. Consumer Product Safety Commission](https://www.cpsc.gov/Recalls).
