# Fixed governance fixture

`pre_c1_authorizations.jsonl` is the unchanged three-record historical book at main commit `2d724930128e225723b56b9ec86282107414afd8`.
SHA256: `49f3564b9512b3d94375d3eb9958012b02dda27e681c13db0efd46cf655e49c4`.

It is a read-only historical input, not an authorization to run anything. Tests needing the pre-B3-C state take its exact two-line byte prefix; they never delete a middle line of the production book. New C1 coexistence/failure probes append explicitly synthetic test events only in pytest temporary directories and never call admin approve or a research runner.
