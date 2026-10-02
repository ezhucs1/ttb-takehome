# Real labels from the TTB Public COLA Registry

Sixty approved Certificates of Label Approval fetched from the public COLA registry at
https://ttbonline.gov/colasonline/ (a public FOIA record), twenty each of distilled
spirits, wine and malt beverages. Public registry data, redistributed here as test input
only. The registry states that label images may differ from the actual labels in type
size, characters per inch and contrasting background.

- `images/`: 97 label panels named `<ttbid>_<panel>.jpg` (front, back, other1, other2),
  resized to at most 1400 px on the long edge and re-encoded as JPEG.
- `applications.csv`: one row per COLA in the batch upload's column format, with the
  registry's own values. Alcohol content and net contents are not on the public record
  and are left blank, so the engine reports them as "left blank on the application" and
  the rows come back for review rather than approval. The class/type column is the
  registry's class name made readable ("Table White Wine", "Other Gin"), which is rarely
  the label's own wording; expect that row to be a review item on most labels. The
  producer name and address are split from the registry's applicant line at the street
  address. The extra columns (`ttbid`, `registry_class`, `note`) are ignored by the app.
- `records.json`: the registry metadata as fetched, one object per COLA, with the
  source URL of each.

Rebuild from registry downloads with `scripts/import_cola.py <folder or zip>...`.

In the app: Applicant > Batch upload > "Real labels from the public COLA registry",
download both files and start the batch. These labels need a live reader (the model, or
the local OCR fallback); demo mode can read only the bundled synthetic samples.
