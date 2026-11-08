# Retired November 8, 2026

Mike, Nov 8 2026: "let's retire the Brevard github job and any legacy systems that aren't necessary for the new site."
Brevard now runs on Safe Ahead Live (https://safeaheadlive.com/fl/brevard/, repo Mjhome25/safe-ahead-live on Render).

- The "Update map" workflow is disabled in GitHub (re-enable it under Actions to run it again).
- The Cloudflare Worker brevard-map-timer has no schedule (`timer/wrangler.jsonc` crons are empty).
- brevardemergency.com and www still answer with a 301 to https://safeaheadlive.com/fl/brevard/. That redirect is a
  Cloudflare setting, not this job, so it keeps working. The `brevardemergency` Worker was left in place.
- The news sweep was already off (commit af95596, Nov 7).
- The history stays here: closure_events.jsonl, audit/ and the git log are kept as they are.

## FHP/FL511 drop trial: closed early here

Mike, Nov 8: finish the trial here now and continue it on Safe Ahead, which runs the same drops.py and the same window
(Nov 3 17:36Z to Nov 10 17:36Z) for Brevard. Final snapshot here is audit/drops.json at 2026-11-08T12:15:47Z:

- 214 drop episodes (213 incidents): FHP 143, FL511 71.
- 182 with nothing found at the same place; 30 with contrary evidence; 2 where only a possibly stale entry remained.
- 36 cases were queued for Mike (forMike) and never reviewed (there is no audit/drop_review.json).
- Groups: crash on a state road 88 (17 contrary); crash on a local street 38 (1); flooding/hazard on a state road
  61 (11); flooding/hazard on a local street 23 (1 contrary, 2 stale); road class unknown 4 (0).

"Nothing found" means no contrary evidence, never proof that the road reopened.
