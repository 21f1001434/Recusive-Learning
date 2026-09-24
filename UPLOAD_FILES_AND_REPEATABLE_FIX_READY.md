# Upload Files + Repeatable Rows Fix Ready

## Verdict

This package fixes the crash:

```text
NameError: name 'build_repeatable_section_plan' is not defined
```

Root cause: the repeatable-row planner was added to the phase summaries, but the phase KB modules did not import `build_repeatable_section_plan` / `apply_repeatable_row_adds`.

## Added

- Imports fixed in Data Map, Document Type, Rules, Transport Profile and BizFlow modules.
- Upload asset support added.
- `uploads/` folder included with the files supplied by the user.
- New CLI option:

```powershell
--upload-assets-dir ".\uploads"
```

- The runner writes:

```text
upload_assets_manifest.json
```

- File-upload fields no longer get marked as skipped by default. The agent now attempts `set_input_files()` on file controls using files from `--upload-assets-dir`.

## Included upload assets

```text
uploads/Configuration_Manual_U-HAUL_ASN.xlsx
uploads/DELLCoXMLASNXX08C.xbm
uploads/o.7x50.260410004040874.XML
uploads/output_u-haul_ASN.xml
uploads/Transform_DELLCoXMLASNXX08C.jar
uploads/UHAUL_4506868691_69D_ASN_VSHIP_20260410124029.xml
```

## Safety

The agent still blocks Save/Create/Submit/Delete/Deploy. Uploading files only attaches them to the open form; it does not save/create portal objects.
