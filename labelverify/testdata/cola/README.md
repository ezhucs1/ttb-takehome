# Real labels from the TTB Public COLA Registry

Sixty approved Certificates of Label Approval fetched from the public COLA registry at
https://ttbonline.gov/colasonline/ (a public FOIA record), twenty each of distilled
spirits, wine and malt beverages. Public registry data, redistributed here as test input
only. The registry states that label images may differ from the actual labels in type
size, characters per inch and contrasting background.

- `images/`: 97 label panels named `<ttbid>_<panel>.jpg` (front, back, other1, other2),
  resized to at most 1400 px on the long edge and re-encoded as JPEG.
- `applications.csv`: one row per COLA in the batch upload's column format. Forty rows
  carry the registry's own values (`scenario` = `registry-as-filed`): alcohol content and
  net contents are not on the public record and are left blank, so the engine reports
  them as "left blank on the application" and the rows come back for review; the
  class/type column is the registry's class name made readable ("Table White Wine",
  "Other Gin"), which is rarely the label's own wording, so that row is usually a review
  item too. The producer name and address are split from the registry's applicant line
  at the street address.
- Twenty rows are a demonstration (`scenario` = `filed-correctly`, `wrong-alcohol`,
  `wrong-net-contents`, `wrong-class`, `wrong-brand`, `near-miss-brand`, `blank-fields`).
  Their application values were transcribed from the label image by a person, then
  altered or blanked for the scenario; none came from the app's reader. The `expected`
  column says what the check should report for a confident model read. The values live
  in `scenarios.json`, which is the file to edit.
- The extra columns (`scenario`, `expected`, `ttbid`, `registry_class`, `note`) are
  ignored by the app.
- `records.json`: the registry metadata as fetched, one object per COLA, with the
  source URL of each.

Rebuild from registry downloads with `scripts/import_cola.py <folder or zip>...`; rewrite
only the CSV after editing the scenarios with `scripts/import_cola.py --csv-only`.

In the app: Applicant > Batch upload > "Real labels from the public COLA registry",
download both files and start the batch. These labels need a live reader (the model, or
the local OCR fallback); demo mode can read only the bundled synthetic samples.
