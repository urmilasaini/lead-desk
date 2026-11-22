# Test fixtures

- **`test_leads.json`** has 18 made-up leads, each with the tag, score range and next action I expected. There is no public data of real buyer inquiries with outcomes (it is private CRM data), so I wrote these in the style of public inquiry datasets, using rough Bangalore prices. They include the awkward cases: Hinglish, a pasted WhatsApp chat, a budget too low for the property, a competitor mention, an NRI buyer, a wrong city and spam.
- **`extracted_facts.json`** has the facts for those 18 leads, as the AI step would return them.

`tests/test_rules.py` scores the facts with `leaddesk/rules.py` and requires at least 17 of the 18 tags to match. It checks the scoring logic only, not how well the AI extracts facts from real messages. The 18th lead (L18) is one I think I labelled wrongly.

All names and numbers are fictional.
