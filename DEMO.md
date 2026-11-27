# Reacalcul · 90-second Hacksgiving walkthrough

**Opening line**

> Same fridge. New answer. A recall can change after you check it, and Reacalcul tells you when an official update adds your exact serial.

| Time | Show | Say |
|---|---|---|
| 0:00–0:12 | Open the [live replay](https://reacalcul-b623b.sprites.app/?alert=example). Start with the two serial tiles. | “Same model, two serials. In January the CPSC added A2210 to the recall. A2401 still fell outside the range.” |
| 0:12–0:32 | Select **Send example to this device**, allow notifications, and let the device alert appear. | “This is a labeled replay of a real notice. That notification went through the same encrypted delivery path as the live watch.” |
| 0:32–0:50 | Point to the quoted range `A2001–A2310`, then show the official notice and refund link. | “Here’s the exact CPSC rule. The owner can see why the answer changed and where to request the refund.” |
| 0:50–1:04 | Select **Try A2401 instead**. Show the outside-range result. | “Changing only the serial changes the answer. For A2401 the system produces no alert.” |
| 1:04–1:20 | Show **Set up my watch**, then the [Sprite health endpoint](https://reacalcul-b623b.sprites.app/healthz). | “The Fly Sprite keeps the saved label and checks the official feed through a Sprites Custom API connector. The next qualifying update can reach the owner after they close the tab.” |
| 1:20–1:30 | Return to the result card. | “Check once. If the answer changes, Reacalcul gets it back to you.” |

Test device permission and the notification before recording. Open a fresh browser profile for each take. The replay remains available in a browser with notifications disabled.

## Submission copy

**Short description**

> Reacalcul saves the model and serial from your Frigidaire minifridge and watches official CPSC recall updates on a Fly Sprite. If an update newly covers your label, it sends an opt-in device alert with the exact range and a link to the official instructions.

**Share draft**

> Same fridge. New answer. I built Reacalcul for @flydotio Hacksgiving. It saves your exact Frigidaire serial, watches CPSC updates on a Fly Sprite, and sends a device alert when a recall expands to include it. Try the two serials in the real January replay. https://reacalcul-b623b.sprites.app/?alert=example

| Item | Link |
|---|---|
| Code | [GitHub](https://github.com/Agozie180/reacalcul) |
| Demo | [Public Fly Sprite](https://reacalcul-b623b.sprites.app/?alert=example) |
| Entry | [Official submission form](https://docs.google.com/forms/d/e/1FAIpQLSfZ_xearoDOSRXI3VjAbM4BJlObUdxSFO0SDwyZ8G6M2lapRQ/viewform) |
| Rules and deadline | [Hacksgiving](https://fly.io/hacksgiving/) · November 29, 2026, before midnight PST |

After the event, publish a short technical write-up on how the Sprite keeps state across deployments and how the watch handles ambiguous prior coverage. Link any placement to Fly.io’s published results before adding it to the project or portfolio.
