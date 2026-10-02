# How it measures against the brief

The brief asks for two deliverables and lists six evaluation criteria. This page maps
each to what is in the repository and the deployed app, and states the shortfalls.

## Deliverables

| Deliverable | Where |
| --- | --- |
| Source code repository | <https://github.com/ezhucs1/ttb-takehome>, one package (`labelverify/`), 378 offline tests, lint-clean |
| README with setup and run instructions | [README.md](../README.md): install, run locally without a key (demo mode and local OCR), demo accounts, three walkthroughs, batch examples |
| Brief documentation of approach, tools used, assumptions made | [ARCHITECTURE.md](ARCHITECTURE.md) (approach and tools), [AI-MODEL.md](AI-MODEL.md) (the model's place, cost and timing), [REGULATIONS.md](REGULATIONS.md) (what is checked and how strictly), [PROTOTYPE.md](PROTOTYPE.md) (assumptions and limits), [decisions.md](decisions.md) (29 decision entries with alternatives) |
| Deployed application URL | Shared with the reviewers directly (not published, to keep the model budget for them): Azure App Service, always on, HTTPS; deployment script in `scripts/deploy_azure.sh` |

## Evaluation criteria

| Criterion | Evidence |
| --- | --- |
| Correctness and completeness of core requirements | The seven fields the brief names plus the Government Health Warning are checked under each class's rules, word for word for the warning, with proof, units and tolerances parsed; every verdict names its reason and its regulation. One test per rule and per sample label; the twenty registry rows with known answers come back as expected ([TESTING.md](TESTING.md)) |
| Code quality and organization | A pure engine with no web imports, a thin web layer whose state changes live in a services package, one rulebook the engine and the UI share, typed pydantic models at every boundary, ruff-clean, 378 tests that run offline in two minutes |
| Appropriate technical choices for the scope | FastAPI, Jinja2, SQLite and one stylesheet: no front-end build, no job queue, no CDN, one container to deploy. A vision model reads; code compares. Each choice and its alternatives are in [decisions.md](decisions.md) |
| User experience and error handling | Three numbered steps, a form that fills itself and says to check it, the label beside the result, plain-language verdicts, an inbox that says what is new, a batch as one bundle. Failed reads say why and offer "Read again"; every model failure falls back to local OCR and says so; batch rows fail individually; error pages are plain HTML ([UI.md](UI.md)) |
| Attention to requirements | Five-second budget measured and shown on every result; "STONE'S THROW" matches "Stone's Throw" and a typo does not; the warning heading must be all caps and bold; batches of 300; nothing loads from a CDN; results are explainable |
| Creative problem-solving | The read happens once at upload and is reused by the pre-check and the submission; the confidence of the read sets the strength of the verdict, so an unreadable photo asks for a better image rather than charging the label; sixty real registry labels with a scenario column turn the batch into a test with known answers; a second provider proves the extractor boundary |

## The interview notes, point by point

| The brief asks for | What is here | Where it falls short |
| --- | --- | --- |
| Results in about 5 seconds; the vendor pilot took 30 to 40 | One model call reads a label set; the comparison is code and takes milliseconds. Measured on the hardest sample (angled, glary phone photo): one panel in 4.1 s, a front-and-back set in 5.5 s. The set is read once at upload; the pre-check and the submission reuse that read. Every result shows its read time; a batch shows elapsed time per label | A two-panel set or a hard photograph can take 5 to 7 s on the model's slow tail. The budget is met for one panel and missed by a second or two for multi-panel photos; the UI never hides it |
| Exact matching, with a word-for-word Government Warning and an all-caps bold heading | The warning is diffed word by word against the statutory text; caps and bold are checked. Text fields match only when identical after normalization; a near miss goes to a person; numbers are compared under each class's tolerance. On real labels, the eight deliberately wrong registry rows are all caught, and the check found two approved labels with defective warnings | The reader, not the engine, is the error source: it can take a brewery name for the brand or misjudge bold type. Both are routed to review rather than decided. Type size cannot be checked |
| Judgment where TTB allows it ("STONE'S THROW" vs "Stone's Throw") | Case, punctuation, spacing, abbreviations and synonyms are folded; a street address matches the label's city and state; a trade or legal name matches whichever is printed; a filed class wrapped in descriptive words goes to a person with the reason | The thresholds are tuned on fourteen synthetic and sixty real labels, not on hundreds with specialist decisions |
| Batch uploads of 200 to 300 | CSV plus zip, up to 300 rows, five concurrent reads in the background, live progress, a summary, every readable row in the queue as one bundle, a results CSV. Rows fail individually | The queue is a thread pool in the web process; a restart mid-batch leaves rows pending (closed as failed at the next start). Cost scales with panels |
| Usable by a novice | Three numbered steps; a prefilled form with a note to check it; a red mark on required fields; the label beside the form and the result; verdicts in plain words with the rule behind each; notices in plain language with a thread on the exact field | No formal Section 508 audit, no email, and the specialist's queue assumes a desktop screen |
| Firewalls that block cloud services | Every read falls back to Tesseract on the server when the model fails, and says so; nothing loads from a CDN; the model endpoint is one setting, with the Azure-tenant path in [PROTOTYPE.md](PROTOTYPE.md) | Tesseract reads the brand, class and net contents on clean artwork and misses small print on photographs |
| A reliable core and documented trade-offs | The seven fields and the warning work end to end for both roles, with the correction loop, in demo mode without any key. The decision log records what was chosen, what was considered, and what two runs on real labels changed | Production needs identity, FedRAMP-authorized hosting, object storage, a durable queue and an evaluation set ([PROTOTYPE.md](PROTOTYPE.md)) |
