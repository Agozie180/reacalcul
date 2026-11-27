# reacalcul

reacalcul remembers the result of a recall check and shows why a later notice changes it for a saved appliance.

The judge demo replays two official U.S. CPSC notices for Frigidaire minifridges. Save model `EFMIS121` with sample code `A2210`. The July 24, 2025 notice leaves the model outside its scope. The January 15, 2026 expansion adds the model for the listed range `A2001–A2310`. Repeat with `A2401` to see the serial-range result. The serial values are examples and the notices are real.

- [July notice](https://www.cpsc.gov/Recalls/2025/Curtis-International-Recalls-Frigidaire-brand-Minifridges-Due-to-Fire-and-Burn-Hazards-More-Than-700-000-Reported-in-Property-Damage)
- [January expansion](https://www.cpsc.gov/Recalls/2026/Curtis-International-Expands-Recall-of-Frigidaire-brand-Minifridges-Due-to-Fire-and-Burn-Hazards)

## Run

Python 3.10+ is sufficient. Run `python3 app.py` and open `http://localhost:8080`.

The HTTP service stores the model, serial and replay state in a SQLite database, keyed by a random browser cookie. A second browser gets its own state. The public app checks only the selected Frigidaire recall series. A current CPSC feed check is available after the replay. That check is a user-triggered request.

## GitHub deployment

The `Deploy to Fly Sprites` workflow deploys every push to `main` and supports manual runs. Set the repository secret `SPRITES_TOKEN` to an authorized Sprites organization token. Set the repository variable `CPSC_CONNECTOR_URL` to the base gateway URL for a Sprites Custom API connector pointing to `https://www.saferproducts.gov`, with `/RestWebServices/Recall` allowed for the `reacalcul` Sprite. The service uses a persistent SQLite path outside each release.

GitHub Actions imports both official notices through the connector, installs the web service on the Sprite, and checks the exact release and both sample outcomes at the public URL. Matching uses the serial ranges in the official text. An unsupported range needs review. The replay continues to work from the verified data when the external feed is unavailable.

A bundled copy of both public CPSC records supports local runs. The official January notice links to the prior notice dated July 24, 2025, even though its unit-count note says July 2024.
