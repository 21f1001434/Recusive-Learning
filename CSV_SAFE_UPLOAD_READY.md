# CSV-Safe + Upload-Ready Patch

## Verdict
This patch fixes the latest failure from the 20260708-173551 run.

The run reached Source Transport Profile and failed while writing:

`source_transport_profile/transport_profile_kb/old_transport_profiles_inventory_with_ids.csv`

Root cause: CSV writers still used direct `path.open(...)`. In the requested deep OneDrive `runs` folder, Windows can raise `FileNotFoundError` for deep output paths even when the logical folder path is valid.

## Fix
Added a shared `safe_write_csv(...)` helper in `hip_id_agent/safe_io.py` and converted all KB CSV writers to use it.

Patched areas:

- Data Map CSV outputs
- Document Type CSV outputs
- Rule CSV outputs
- Transport Profile CSV outputs
- BizFlow CSV outputs
- Summarizer CSV outputs

The helper:

- creates parent folders before writing
- supports Windows long-path fallback using the `\\?\` prefix
- preserves UTF-8 and newline-safe CSV writing

## Upload files
The package includes the upload assets under `uploads/`:

- `Configuration_Manual_U-HAUL_ASN.xlsx`
- `DELLCoXMLASNXX08C.xbm`
- `o.7x50.260410004040874.XML`
- `output_u-haul_ASN.xml`
- `Transform_DELLCoXMLASNXX08C.jar`
- `UHAUL_4506868691_69D_ASN_VSHIP_20260410124029.xml`

The command should keep using:

```powershell
--upload-assets-dir ".\uploads"
```

## Output folder
`config.yaml` is set to:

```text
C:\Users\Adheesh_Srivastava\OneDrive - Dell Technologies\Desktop\VishnuBaghvan\Browser Testing\HIP_Chatbot\hip_portal_id_agent_kg\runs
```

No `--runs-dir` is required.
