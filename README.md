# bottled-lyftr

[Lyftr](https://github.com/Cawlumm/lyftr) is a self-hosted workout,
weightlifting, bodyweight and nutrition tracker, packaged as a Cloud in a
Bottle app. One deployment belongs to one person, and all of the data stays on
your zone.

## What you get

- Log workouts set by set, recording reps and weight for each set. Every
  workout keeps its date, how long it took and any notes you add, so past
  sessions stay browsable.
- Build programs (reusable routines with days and target sets) and follow them.
  When a session beats the targets, the app stages progression suggestions on
  the routine that you can apply or dismiss, one at a time or all at once.
- Run a guided gym session: one exercise at a time, full screen, with a rest
  timer between sets.
- Browse an exercise library of over 800 movements, each with step by step
  instructions, the equipment it needs, and the muscles it targets. Every
  exercise page shows your best set for that movement, an estimated one rep
  max, and a chart of how the weight you lift has progressed.
- Track bodyweight over time on a chart, with your current weight, the change
  across the period you pick (7 days, 30 days, 90 days or all time), and the
  average, low and high for that period. Weights display in lbs or kg.
- Log food with calories and macros (protein, carbs, fat) against daily
  targets, by name search or barcode lookup, and keep a list of saved foods for
  meals you repeat.

Single user per deployment.

## Usage

Open the app and you are already signed in as the owner. There is no login
step and no password to manage.

The app is owner-only. It is not shared publicly, and anonymous visitors
cannot reach it.

## Caveats

- Outbound internet access is required for two features: the exercise library,
  which is downloaded on first boot, and food search plus barcode lookup, which
  query Open Food Facts on each request. Without outbound access the library
  stays empty and food search does not work. Exercise illustrations are loaded
  from the public dataset when you view them, so they need network access from
  your device as well.
- The exercise library seeds in the background, so it can be empty for the
  first minute or two after a fresh deploy. It fills in on its own; reload the
  page to pick it up.
- Bodyweight allows one entry per calendar day. Logging again on the same day
  updates that day's entry rather than adding a second one.
- Single user only. There is no sharing, no second account and no way to invite
  anyone else.

## Data

Everything persists under `$OPENHOST_APP_DATA_DIR`:

- the SQLite database holding your workouts, programs, bodyweight entries and
  food logs,
- the app's JWT signing key, kept so your session survives a restart,
- a small file recording the owner's numeric user id.

SQLite keeps write-ahead-log side files next to the database, so back up the
whole directory rather than the database file alone.

## Resources

512 MiB of RAM and 0.5 CPU cores.

## License

Upstream Lyftr is MIT licensed (Copyright (c) 2026 cwlumm) and this packaging
is MIT as well. See [LICENSE](LICENSE) for the full text and
[NOTICE](NOTICE) for attribution and the upstream source offer.

Food search and barcode results come from
[Open Food Facts](https://world.openfoodfacts.org), whose data is licensed
under the Open Database License (ODbL). The seeded exercise dataset,
[free-exercise-db](https://github.com/yuhonas/free-exercise-db), is public
domain.
