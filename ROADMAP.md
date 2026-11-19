# Roadmap

NetSec AI, as of 2026-10-29. Done: the fork from PadhAI, the engineering assistant's prompt and behaviour eval
(`backend/scripts/eval_engineering.py`), the Engineering Profile API, secret masking before the LLM and
embeddings providers, the local security gate, Graphify, and NetSec's isolated deployment (`deploy-netsec/`).

## Next

1. **Knowledge base:** ingest the owner's documents (`backend/scripts/ingest_documents.py`, with `--metadata` such
   as vendor, platform, version, topic), then calibrate `RAG_MIN_SCORE` with `eval_retrieval.py` and decide how
   retrieval uses the Engineering Profile (today `retrieval_for` still reads the retired school fields).
2. **Certification question bank:** replace PadhAI's NCERT content in `qbank/` with certification domains and topics,
   then decide on `TUTOR_INTERACTIVE_BLOCKS` (chat tests and widgets).
3. **Engineering Profile in the web app:** finish and release the Settings section (in the working tree on
   2026-10-29), then use the profile in retrieval (item 1).
4. **A named Cloudflare tunnel** with a fixed hostname (`deploy-netsec/cloudflared/config.yml.template`), so sign-in
   stops breaking when the Quick Tunnel's address changes.
5. **CI:** restore GitHub Actions credit (or another runner); until then `bun run security` and the hand-run suites
   are the only checks.
6. **Production security follow-ups:** the open items in
   [docs/security/production-security-checklist.md](docs/security/production-security-checklist.md) and the audit
   ([docs/security/security-audit.md](docs/security/security-audit.md)).
7. **Intent router and scope filter for NetSec:** new evaluation cases and a `--sweep` for the 2026-10-29 taxonomy
   before either is turned on.
8. **Interactive learning blocks (built 2026-11-18, dormant in production).** Each item needs its own owner decision:
   - **Prompt teaching** for the new blocks: the quiz kinds and modes, cheat sheets, video and topology. Teaching
     cheat sheets also needs a bank-mode guard (`chat_tests._card_exercises` would turn a sheet holding `[[x]]` into
     an exercise).
   - **Question-bank support** for the new kinds, and a bank mock exam.
   - **Stream redaction of exam keys:** today they are readable in the reply's text (devtools, network).
   - **An exam timer.**
   - **A lab block** (today `:::steps` and code Copy cover it).
   - **Video from the File Library.**
   - **Spaced repetition** for flashcards.

## Standing follow-ups

Off-host, encrypted backup copies (an owner decision, not yet made); a staging environment; backend error reporting; alerting; non-root services; masking for
Ollalink requests (or keeping Ollalink off); re-verifying the PadhAI-era docs listed in [docs/README.md](docs/README.md).
