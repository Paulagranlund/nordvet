# No-shows at Nordvet: what the data shows

*Based on VetOne data from 6 August to 14 November 2026 (about 67,000 past appointments at 39 clinics).*

## Summary

1. **Odense C's no-show rate has roughly tripled since early October**, from 6–8% in August–September to about 20% per week.
2. **Hillerød, Aarhus C and København V have had about 3× the no-show rate of the rest of the chain** throughout the period (19–23% vs 7.5%).
3. **Appointments booked far ahead are much more likely to become no-shows**: 17% for bookings made 15+ days ahead, compared with 3% for same-day bookings. 
4. **The daily dashboard makes changes like Odense C's visible within days**, instead of when someone notices.


## Findings in detail

**8.5% of expected visits are no-shows, and the rate is stable.** This corresponds to about 130 lost vet-hours a week, or roughly 6,900 a year: the working time of three to four full-time vets. The data has no prices, so the cost to Nordvet is *lost vet-hours × what a vet-hour is worth to them*.

**Three clinics stand out: Hillerød (23%), Aarhus C (22%) and København V (19%).** The rest of the chain averages 7.5%. The pattern holds for every vet, every service and every booking lead time at those clinics, so it points to how these clinics work (for example reminders or booking routines) rather than to their patients or individual vets. If they came down to the same no-show rate as the other clinics, Nordvet would get back about **830 vet-hours a year**.

**Odense C is getting worse.** It went from 6–8% in August–September to 15–21% since early October. The rise is concentrated on two of its three vets' calendars. The data points to a change around the start of October, which needs more context to explain.

**The further ahead an appointment is booked, the more likely a no-show.**

| Booked | Same day | 1–3 days | 4–7 days | 8–14 days | 15+ days |
|---|---|---|---|---|---|
| No-show rate | 3% | 7% | 9% | 9% | **17%** |

**Follow-ups (12.5%) and vaccinations (10%) are skipped most; surgery (3%) rarely.** Consultations lose the most hours overall because there are so many of them. Mondays (10.5%) and 8 am slots (11%) are a little worse than average.

## Checked and ruled out

- **Not a few "serial" no-show owners.** An animal that missed its last visit is only slightly more likely to miss the next one (11% vs 8%). This shifts the focus away from individual owners and towards how clinics book and remind.

- **Not slow registration at some clinics.** Every clinic records no-shows equally late (about a day after the appointment), so comparing clinics is fair.

- **"Cancelled after the appointment time"** (about 1,100 cases, 0.5–2.5% at every clinic) are kept separate and not counted as no-shows. They are spread evenly, so they don't change which clinics stand out. Worth asking the clinics what these are.

## Good to know when reading the numbers

- **The last working day's numbers are provisional.** No-shows are typically recorded about a day late, so appointments with no outcome yet can turn into no-shows over the next few days.
- **No-show rate = no-shows ÷ (completed + no-shows).** Cancelled appointments are left out, since nobody was expected.
- **Deleted appointments are excluded.** Ystad (inactive, no appointments since September) is included in history but not in daily reports.

---

*Every number in this document can be reproduced with `python -m analysis.findings`.*
