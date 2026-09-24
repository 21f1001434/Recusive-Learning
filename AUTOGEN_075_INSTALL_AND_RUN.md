# Install and run with Microsoft AutoGen 0.7.5

Install exact dependencies:

```powershell
python -m pip install -r .\requirements.txt
```

The requirements pin:

```text
autogen-agentchat==0.7.5
autogen-core==0.7.5
autogen-ext[openai]==0.7.5
```

Verify the AutoGen runtime:

```powershell
python -c "from hip_id_agent.autogen_runtime import assert_autogen_075; print(assert_autogen_075())"
```

Run the complete autonomous mission:

```powershell
.\RUN_ASSURED_AGENTQ_FULL_E2E.ps1 -RunsDir "C:\hip_runs" -ApiMode capture
```

Run the Streamlit UI:

```powershell
.\RUN_STREAMLIT_ASSURED_AGENTQ.ps1 -InstallDependencies -Port 8501
```
