# WFM Taxonomy (contact-center workforce management)

**Status: v0 draft. Have a working WFM practitioner review it before any public release.**

Read this before extracting requirements when the JD or resume is about workforce management, forecasting, scheduling, real-time/intraday, or capacity planning. It was built from general domain knowledge plus a small sample of public WFM postings (about 15, Jun to Nov 2026). Tags: **[S]** seen in sampled postings, **[D]** domain knowledge only.

Every "same capability" call below is a judgment, not a fact. The grounding rule always wins: when in doubt, ask the candidate instead of crediting.

## 0. Which "WFM"?

Two different fields share the label.

- **Contact-center WFM:** forecasting contact volume, scheduling agents, intraday/real-time management, capacity planning. Platforms: NICE, Verint, Genesys, and others (section 2). [S]
- **Labor management / timekeeping WFM:** time and attendance, labor scheduling, pay rules, union timekeeping. Platforms: UKG (Kronos) and similar. [S: a "UKG WFM Analyst" posting]

Experience in one does not satisfy the other. If the JD or resume is ambiguous, ask one question before scoring.

## 1. Matching rules for this domain

1. **Generic is satisfied by specific, never the reverse.** "WFM platform experience" is met by any named platform on the resume. "Verint" is not met by NICE. Same for "BI tool" and Tableau, or "time-series forecasting" and exponential smoothing.
2. **Named platforms never earn synonym credit for each other**, even inside the same category.
3. **Alternatives the JD itself offers** ("NICE, Verint, or Genesys"; "Excel and/or Google Sheets") count as one requirement, met at full credit by any listed option. State this in the report. (In script mode, list the options under `alternatives`; the collector builds these groups automatically from "or", "and/or", and "such as" lists.)
4. **Vendor renames and families** (NICE IEX, CXone, inContact appear side by side in postings): whether a candidate's experience with one satisfies another is a question for the candidate, not an assumption.
5. **Ambiguous acronyms:** resolve from context or ask. Expand on first use.
6. **Metric words are not interchangeable** unless section 3 says so.

## 2. Platforms (each is its own product, no cross-credit)

| Family | Names you will see | Tag |
|---|---|---|
| NICE | IEX, CXone, inContact, NICE WFM | S |
| Verint | Verint WFM, Verint Workforce Management | S |
| Genesys | Genesys Cloud (WFM) | S |
| UKG | UKG WFM, Kronos (labor management, see section 0) | S |
| Calabrio | Calabrio WFM, Calabrio ONE | D |
| Alvaria | Alvaria WFM | D |
| Assembled | Assembled | D |
| Injixo | Injixo | D |

## 3. Metrics

| Term | Also written | Not the same as / note |
|---|---|---|
| AHT | average handle time, handle time | Talk + hold + after-call work. Not "talk time". |
| ASA | average speed of answer | Not abandonment, not service level. |
| Service level | SL, targets like "80/20" | JDs sometimes write "SLA" for it. Treat SLA = service level only when the JD and resume use it for the same metric, otherwise ask. |
| Abandonment rate | abandon rate, abandoned calls % | Same metric. |
| Occupancy | occupancy rate | Not utilization. Ask which definition if only one side says it. |
| Utilization | utilization rate | Not occupancy. |
| Adherence | schedule adherence, real-time adherence | Checks activity against the schedule by interval. Not conformance. |
| Conformance | schedule conformance | Checks total time worked against scheduled time. Not adherence. |
| Shrinkage | | Paid time not available for contacts (breaks, training, PTO, meetings). Not attrition. |
| Attrition | turnover | Not shrinkage. |
| FTE | full-time equivalent | Not headcount. |
| Forecast accuracy | | Generic. Named measures (MAPE, WAPE) are different from each other. |

## 4. Functions and processes

| Term | Same capability (OK to swap) | Related but different (ask first) |
|---|---|---|
| Forecasting | volume, contact, workload, or demand forecasting | capacity planning |
| Scheduling | schedule creation, schedule building, shift scheduling | shift bidding (a specific activity), workforce planning |
| Intraday management | real-time management, real-time monitoring | forecasting, scheduling |
| Capacity planning | long-term staffing plan | headcount modeling, hiring plan (close, but postings list them separately) |
| Workforce planning | | capacity planning (workforce planning can be broader or org-wide) |
| Time-off management | PTO management, leave management | scheduling |

**RTA** can mean real-time adherence (a measure) or real-time analyst (a role). Resolve from context.

## 5. Methods (different methods are never synonyms)

- **Staffing math:** Erlang C, Erlang A, Erlang B, simulation. "Erlang" alone: ask which, or whether the tool handled it.
- **Forecasting:** time-series methods (moving average, exponential smoothing, ARIMA, seasonal decomposition) versus regression. A generic "time-series forecasting" requirement is met by any named time-series method, not the reverse.

## 6. Analytics tools (named tools never swap)

|Excel (including pivot tables), Google Sheets, SQL, Python, R, Tableau, Power BI, MicroStrategy, Looker. Excel is not Google Sheets unless the JD lists them as alternatives. A generic "BI" or "data visualization" requirement is met by any named tool on the resume.

## 7. Titles and levels

- **Same family (title synonyms):** WFM Analyst = Workforce Management Analyst = Workforce Analyst. Real-Time Analyst = Intraday Analyst = RTM Analyst. Forecasting Analyst = Forecast Analyst.
- **Partial:** Capacity Planner vs Forecasting Analyst. WFM Scheduler vs WFM Analyst. Workforce Planner vs WFM Analyst (often longer horizon).
- **Different:** WFM Analyst vs BI/Reporting Analyst, HR or People Analytics, or labor-management/timekeeping analyst (section 0).
- **Levels (I, II, Senior, Lead) are not a standard ladder.** The sample had an "Analyst I" asking 2 to 5 years and an "Analyst II" asking 3 to 5, a real-time analyst role asking 3+, and entry roles asking 1+. [S] Use the JD's own number, never infer years from the level word.

## 8. Environment and context

- **Call center = contact center** (same).
- **"Managing outsourced contact center operations"** [S] is a vendor-management claim. Having worked at a BPO does not satisfy it. Ask.
- **Multi-site / multi-client:** needs evidence of the count. Ask if the resume does not give one.
- **Channels:** "omnichannel" is met by named channels (voice, chat, email, back office), not the reverse.
- **Hours, shift, and location** (24/7, offshore, client site) are "confirm with candidate" items. Do not score them.

## 9. Gap-question templates

- **Platform:** "The JD asks for [Verint]. Your resume shows [NICE IEX]. Have you used [Verint] itself? If so, where, and for what?"
- **Method:** "The JD mentions Erlang. Which variant did you work with, or did the platform handle it?"
- **Metric:** "The JD lists occupancy and your resume says utilization. Same calculation?"
- **Scale:** "How many agents, queues, sites, or clients did you plan for?" (Feeds the `[INSERT: ...]` placeholder, never a guess.)
- **Vendor management:** "The JD wants experience managing outsourced operations. Did you manage a vendor's schedules or SLAs, or work inside one?"

## 10. Questions for the practitioner reviewer

1. Are the same-capability calls right, especially intraday vs real-time management and capacity planning vs headcount modeling?
2. Which platforms and renames are missing (healthcare, government, BPO, retail)?
3. Is the title list complete, and are the partial/different calls fair?
4. What would a hiring manager add that a keyword scan misses?
