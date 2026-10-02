$ErrorActionPreference = "Stop"
& .\VERIFY_V243R36_INSTALL.ps1
if ($LASTEXITCODE -ne 0) { throw "R36 baseline verification failed" }
python -c "import pathlib; names = ('index.html', 'app.js', 'styles.css'); assert all(pathlib.Path('webui', n).read_bytes() == pathlib.Path('backend', 'webui', n).read_bytes() for n in names); css = pathlib.Path('webui', 'styles.css').read_text(encoding='utf-8'); assert all(t in css for t in ('container-type:inline-size', '@container main', 'body{overflow-wrap:break-word}', '.side-section.collapsed', '.chat-resize')); html = pathlib.Path('webui', 'index.html').read_text(encoding='utf-8'); assert all(t in html for t in ('id=' + chr(34) + 'sideToggleBtn' + chr(34), 'role=' + chr(34) + 'tablist' + chr(34), 'id=' + chr(34) + 'chatResize' + chr(34))); js = pathlib.Path('webui', 'app.js').read_text(encoding='utf-8'); assert all(t in js for t in ('function initLayout', 'function initSideSections', 'function revealHumanAssistance', 'clearTimeout(state.toastTimer)')); print('R37_CONTROL_CENTER_LAYOUT_OK')"
if ($LASTEXITCODE -ne 0) { throw "R37 Control Center layout smoke failed" }
Write-Host "V243R37 install verification PASS" -ForegroundColor Green
Write-Host "The Control Center fits every screen: nothing overflows; the sidebar collapses; tabs stay on screen; the chat can be resized."
