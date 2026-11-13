# Architecture

[mothusi-aws-map.html](mothusi-aws-map.html) is the architecture map. Open it in a browser. It is also
published as a private artifact: https://claude.ai/artifact/HqxSETwDfdkEtaFkHCrwHq

It holds:

- the project description and the learner side;
- the stretch features (practice tests and the exhibition hall);
- five hosting options, each drawn as its own diagram;
- an AWS cost calculator for testing plus the 4-hour judging window;
- the model picks, the component table and the POPIA notes;
- the dated build order and the sources, each with the date it was read.

The decision taken from it is [ADR 0001](../adr/0001-aws-cape-town-open-models.md): option A, AWS
af-south-1 with open-weight models on an EC2 G4dn. Prices and AWS facts were read on 2026-11-10; check
the dated ones again before they go on a slide.

[hosting-options.md](hosting-options.md) keeps all five options in plain markdown: what each runs, what
leaves South Africa, its costs, when to use it, and how to switch to it.

The page is a single self-contained HTML file. Edit it directly, and keep the published artifact in step.
