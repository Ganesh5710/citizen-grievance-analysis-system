# System Usability Scale (SUS) Evaluation Questionnaire

Standard 10-item 5-point Likert questionnaire (John Brooke, 1996) prepared for pilot usability evaluation across Citizen, Employee, District Admin, and Super Admin test participants.  
**Scale:** `1 = Strongly Disagree`, `2 = Disagree`, `3 = Neutral`, `4 = Agree`, `5 = Strongly Agree`.

## Questionnaire Items

| # | Statement | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| 1 | I think that I would like to use the Citizen Grievance Portal frequently. | [ ] | [ ] | [ ] | [ ] | [ ] |
| 2 | I found the portal unnecessarily complex. | [ ] | [ ] | [ ] | [ ] | [ ] |
| 3 | I thought the complaint submission and tracking workflows were easy to use. | [ ] | [ ] | [ ] | [ ] | [ ] |
| 4 | I think that I would need the support of a technical person to be able to use this system. | [ ] | [ ] | [ ] | [ ] | [ ] |
| 5 | I found the various functions (location lookup, duplicate alerts, QR tracking, reports) well integrated. | [ ] | [ ] | [ ] | [ ] | [ ] |
| 6 | I thought there was too much inconsistency across dashboards and terminology. | [ ] | [ ] | [ ] | [ ] | [ ] |
| 7 | I would imagine that most people would learn to use this portal very quickly. | [ ] | [ ] | [ ] | [ ] | [ ] |
| 8 | I found the portal very cumbersome to use. | [ ] | [ ] | [ ] | [ ] | [ ] |
| 9 | I felt very confident using the portal (pincode auto-fill, feedback, status updates). | [ ] | [ ] | [ ] | [ ] | [ ] |
| 10 | I needed to learn a lot of things before I could get going with the system. | [ ] | [ ] | [ ] | [ ] | [ ] |

## SUS Score Calculation Formula

- For odd-numbered items (1, 3, 5, 7, 9): score contribution = `(rating - 1)`.
- For even-numbered items (2, 4, 6, 8, 10): score contribution = `(5 - rating)`.
- Total SUS Score = `sum(contributions) * 2.5` (range `0` to `100`; benchmark threshold `>= 68` above average).

## Pilot Results Log Template (To Be Completed During Live User Study)

| Participant ID | Role Tested | SUS Score (0-100) | Qualitative Feedback / Friction Notes | Date |
|---|---|---|---|---|
| P-01 | Citizen | _Pending field trial_ | | |
| P-02 | Employee | _Pending field trial_ | | |
| P-03 | District Admin | _Pending field trial_ | | |
| P-04 | Super Admin | _Pending field trial_ | | |

*(Honest disclosure: No human-subject SUS study responses were collected during automated engineering development; this template is ready for live evaluation.)*
