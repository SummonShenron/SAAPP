# Knowledge-base documents about Sonic itself

`sonic_assistant_architecture.html` is the source of the reference document that Sonic answers its own "how do you work?" questions from (it is what
people without access to the repo can ask about). `sonic_assistant_architecture.pdf` is the file that gets uploaded: the ingestion pipeline
(`local_function_app/function_app.py`) only accepts PDFs and images.

Regenerate the PDF after editing the HTML (headless Edge, plain text layer so it chunks cleanly):

    msedge --headless --disable-gpu --user-data-dir=%TEMP%\edge-pdf --no-pdf-header-footer --print-to-pdf=docs\kb\sonic_assistant_architecture.pdf file:///<repo>/docs/kb/sonic_assistant_architecture.html

To publish it: Self Service, Jack's portfolio, delete the old `sonic_assistant_features.pdf`, upload the new PDF. Keep it true: when a feature is added,
removed or renamed, change the matching section here, in the same change as `backend/components/sonic_changelog.py`.
