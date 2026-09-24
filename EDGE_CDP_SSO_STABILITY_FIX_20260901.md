# v1.8.8 Edge/CDP/SSO stability hotfix

This hotfix addresses the live Windows issue where Microsoft Edge opened but Browser-Use/cdp-use repeatedly lost the CDP websocket during Dell SSO, preventing the mission from progressing.

Changes:
- Microsoft Edge remains the single browser lifecycle owner through the primary Playwright persistent context.
- Browser-Use and LangChain CDP clients are not attached at application startup by default. They attach lazily for recovery perception only.
- Browser-Use attach and snapshot operations have strict time bounds and fail open to deterministic Playwright.
- CDP `/json/version` is health-probed before MCP/auxiliary attachment.
- Browser MCP agreement is deferred while Dell SSO is active; MCP is re-verified after the authenticated HIP surface returns.
- No Selenium/ChromeDriver dependency is required. `chromium` and `chromedriver-py` installed through pip are unrelated to the application browser path.

Live verification sequence is documented in the assistant response accompanying this package.
