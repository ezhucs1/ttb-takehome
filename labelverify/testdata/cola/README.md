# Real labels from the TTB Public COLA Registry

Sixty approved Certificates of Label Approval fetched from the public COLA registry at
https://ttbonline.gov/colasonline/ (a public FOIA record), twenty each of distilled
spirits, wine and malt beverages. Public registry data, redistributed here as test input
only. The registry states that label images may differ from the actual labels in type
size, characters per inch and contrasting background.

- `images/`: 97 label panels named `<ttbid>_<panel>.jpg` (front, back, other1, other2),
  resized to at most 1400 px on the long edge and re-encoded as JPEG.
- `applications.csv`: one row per COLA in the batch upload's column format. Forty rows
  carry the registry's own values (`scenario` = `registry-as-filed`). Every label in the
  set was approved by TTB, yet most of these rows come back "corrections needed", because
  the public registry does not publish what the applicant typed on the form. It
  publishes the brand name (usually as printed, but sometimes what the permit holder
  filed: the Spartan Select can is registered under "Saugatuck Brewing Co."); the
  class/type as a category code ("Other Gin", "Table White Wine"), not the designation
  printed ("American Gin" was approved under "Other Gin"); the permit holder's legal name
  and street address, often not the name on the label (Delicato Vineyards holds the COLA
  for the Francis Ford Coppola Winery label); and no alcohol content or net contents at
  all. So the class row mismatches, the blank rows ask for the value, and the producer
  and address rows mostly match after the engine accepts a street address against a
  city and state and a trade or legal name against whichever is printed. That is the
  check working on filed values that do not match the label, not a reading error. The
  producer name and address are split from the registry's applicant line at the street
  address.
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
