# Problem Statement

> The assignment brief as received. Kept here unchanged in meaning, so the README and the code can be read against it.

# Masal AI: FDE Assignment (Round 2)

Build a small AI-powered web app that helps a real-estate salesperson prioritize inbound leads and act on them. One required feature is entirely up to you to invent, and we'll dig into your decisions together in a 30-minute interview.

## Role Context & What We're Testing

You'd be sitting between our product and real customers, configuring, demoing, and troubleshooting AI voice systems live, often on a call with someone non-technical. That means two things both have to be true: you can build working software quickly, and you can explain what you built clearly enough that a buyer trusts it.

This assignment tests both:

- **Building:** can you turn an ambiguous brief into a working product in a few hours, using an AI model as a real product component rather than a demo gimmick.
- **Explaining:** in your README, your 3-minute demo, and a live 30-minute interview where you'll walk us through your own code.

## The Assignment

**Scenario.** A real-estate sales team receives hundreds of leads a day. A salesperson needs to quickly see which leads matter, what the customer actually wants, and what to do next.

### Core requirements (all required)

1. **Lead intake.** A form to enter Name, Location, Property requirement, Budget, Buying timeline, and a free-text Customer message (e.g. an inquiry or chat transcript).
2. **AI analysis.** Call an AI model to generate: Lead summary, Customer intent, Key requirements, Objections/concerns, Recommended next action, and a Suggested response to the customer.
3. **Conversational interface.** The salesperson can ask follow-up questions about a lead ("what should I emphasize on the call?", "make my reply more assertive") and get answers grounded in that lead's context, not a generic chatbot.
4. **Lead list & prioritization (required).** Don't build only a single-lead form. Support multiple saved leads, in-memory or a real database, your call, each showing its AI analysis, and rank or group them by some notion of priority or quality your AI analysis produces (a lead score, a hot/warm/cold tag, an urgency flag, you decide).
5. **Clear display.** A salesperson should be able to scan the AI output in seconds.

### Your own feature (required, content is up to you)

Add at least one feature you believe would genuinely help a salesperson in this workflow. We're intentionally not specifying it. Think about what happens before, during, and after this screen in a real salesperson's day.

### Technical requirements

- At least one real AI API/model call, not a hardcoded or canned response.
- Deployed at a live URL we can open and test with no setup.
- A public GitHub repository.
- Free-tier resources only (Gemini API, Groq, OpenRouter, Hugging Face, Ollama locally, Vercel, Netlify, Cloudflare Pages, GitHub Pages, or an equivalent).
- AI coding assistants (ChatGPT, Claude, Cursor, Copilot, etc.) are expected, but you must understand and be able to explain every part of what you submit.

## Submission Requirements

Submit all four:

1. **GitHub repository (public)**, with a README covering: what you built, an architecture overview, which AI model/API you used and how it's called, how to run it locally, key technical decisions, known limitations.
2. **Live URL** we can open and test without setup.
3. **Demo video, max 3 minutes**, screen-recorded, showing: the product, the main workflow end to end, your own added feature, and one interesting technical or product decision you'd want to discuss. Loom, Google Drive, or an unlisted YouTube link all work.
4. **AI usage disclosure:** which AI tools you used and for what (e.g. "Claude for prompt design and the initial React scaffold, Copilot for autocomplete, ChatGPT to debug a CORS issue").

**How to submit:** a Google Form link will be shared with you. Make sure all live links shared on the form are open for anyone with the link.

---

## Where each requirement lives in this repo

| Requirement | Where |
|---|---|
| 1. Lead intake | **Add lead** form, and **Import CSV** for many at once (`static/index.html`, `static/app.js`, `static/lib.js`) |
| 2. AI analysis | `leaddesk/prompts.py` (EXTRACT, ANALYZE) → `leaddesk/service.py`; shown on the lead page |
| 3. Conversational interface | Floating chat widget on the lead page → `leaddesk/service.py` `chat()` with the lead's full context |
| 4. Lead list & prioritization | **Leads** board (Hot / Warm / Cold / Disqualified), score from `leaddesk/rules.py`; **Pipeline** board by deal stage |
| 5. Clear display | Next action first, +/− reasons, requirement chips, score bars; **How it works** page |
| Own feature | Today's call plan with a response clock, **Log call** with live re-scoring, WhatsApp reply, change priority with a reason |
| Real AI call | `leaddesk/ai.py`. Nothing is pre-filled or canned: every analysis comes from a live call |
| Tests | `tests/` (run `python tests/run_all.py`) |
