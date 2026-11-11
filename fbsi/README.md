# fbsi: the Felixa Behavioural Safety Intelligence detectors

These eight files are the FBSI engine of **Felixa-Behavioural-Intelligence** (`backend/`), copied **byte for byte**. They are never edited here, so a newer engine can replace them
file by file. Their relative imports (`from . import be_acts`) work unchanged inside this package. The adapter that feeds them is `safety_module.py` in the project root.

| File | SHA-1 of the original |
|---|---|
| be_acts.py | de3c5a093dda3c5165e1c6e96cbaf3eaa88683ce |
| be_catalogue.py | fccc8715b11492ac2d7dd7a1866e640e3975ee96 |
| be_detectors.py | c856814698d524de4a42f6d215a47677e9b8bfcf |
| be_ledger.py | 84ea9409012c709942a1b1b40818bf7b00bfe775 |
| be_release.py | 4ea082d2b0bbf01b1663c7ec1d5adf29f2e30ae9 |
| be_safeguarding.py | 30afbf912af24e50dbe539781eaa88bf9145a94c |
| be_stance.py | 39ad3bb1e19391fccbd10874f440719832b64f4c |
| org_signals.py | 558a7b3b57141ff8c7407b25a04edfba3e8f8fae |

Not copied: `be_grooming.py` (shadow-only grooming reading), the 55-alert reference data (`fbi_safety_55.json`) and the language-model reader (`be_llm_acts.py`).

**Status, as the algorithm states it:** shadow only, unvalidated, English only. No live Safety alert exists without a named qualified safeguarding reviewer, an independent second reviewer and an approved
emergency protocol (`be_safeguarding.py`), so everything the module finds is *held*: a candidate for a person to read, never operational.
