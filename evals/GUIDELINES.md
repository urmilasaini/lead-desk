# Labeling guidelines

These rules decide every gold value. They were written before the labels, so a second person can label the same way.
Unstated means the neutral default: `null`, `false`, `"unknown"` or `0`.

| Field | Rule |
|---|---|
| `is_spam` | True only if the sender is not a property buyer (vendor, job seeker, empty junk). A vague buyer is not spam. |
| `primary_location` | One of the named areas, or the city if only the city is given. If several areas are listed and none is chosen, `null` and `multiple_locations=true`. |
| `property_type` | `1BHK`..`4BHK`, `villa`, `plot`. Words and digits mean the same ("three bhk" = 3BHK). |
| `budget_lakhs` | The buyer's own stated budget in lakhs (1 crore = 100). Upper end of a range, with `budget_range` set. Not a competitor's price, loan amount, per-sqft rate, area or phone number. |
| `timeline_months` | Months until the buyer wants to buy or move. "Next week" = 1, "next year" = 12, ranges use the upper end. "Call me today" is not a timeline. |
| `has_reason` | A concrete trigger: lease ending, transfer, school, baby, new job. "Prices only go up" is not one. |
| `loan_status` | `sanctioned`, `pre_approved`, `in_process`, `none` (not applied), `own_funds`, or `unknown`. |
| `visit_or_call_requested` | The buyer asks for a call, visit or video call. Refusing a call, or visiting only after something else, is false. |
| `already_visited` / `ready_to_book` | Stated as done, or an explicit wish to book/finalise. |
| `decision_maker` | `needs_others` if they must ask someone, `joint_present` if the other decision maker is part of the enquiry. |
| `n_questions` | Direct questions the buyer asks (not the agent). |
| `concrete_details_count` | Count of: location, type, budget, timeline, loan status known, reason. Used as a rough richness check only. |

`ambiguous_fields` on a lead lists fields where two reasonable readers could differ; they are not scored for that lead.

Tag expectations (checked through `rules.py`, not guessed): Hot 70+, Warm 40-69, Cold below 40, Disqualified for spam or out of area.
