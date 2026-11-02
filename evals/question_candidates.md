# Level 1 — Candidate golden-set questions (NO SQL yet)

Status: DRAFT for you to edit. Strike, reword or add questions, then write and verify the gold SQL yourself.
Built from the actual marts (see CLAUDE.md). Data covers ONE period, so there are no year-over-year questions.

Tags: `E` easy (one table) · `M` medium (join / group / having) · `H` hard (window / CTE / multi-step)
· `A` ambiguous (agent must ask for clarification) · `U` unanswerable (agent must say the data cannot answer it)
Suggested **holdout** (never look at these while tuning prompts): marked ★.

Pitfalls to decide BEFORE writing the gold SQL (put your decision in `notes`):
- `avg_*` columns in `fct_medicare_drg` are per-discharge averages. Hospital/state/DRG totals need `avg * total_discharges`. "Average payment per discharge" across hospitals should be discharge-weighted.
- Percent-of-revenue questions: ratio of sums vs average of per-hospital ratios give different answers. Pick one per question.
- `charity_pct_of_revenue` etc. are fractions (0.05 = 5%).
- ~138 hospitals have no CBSA, so geography joins must be LEFT joins if you want them included.
- `mart_reimbursement_gap` / `mart_service_line_margin` already exclude ~19 hospitals with no usable cost-to-charge ratio, and outliers.
- `hospital_overall_rating` may contain non-numeric text. Check its type before ranking.

## Easy (15)

| id | question | main tables | watch out for |
|---|---|---|---|
| q001 | Which 10 hospitals have the most beds? | mart_finance_scorecard | NULL beds; ties |
| q002 | How many hospitals are there in each state? | dim_hospital | order matters |
| q003 | How many hospitals have a 5-star overall rating? | dim_hospital | rating type |
| q004 | Which 10 hospitals have the highest net patient revenue? | mart_finance_scorecard | |
| q005 | How many hospitals reported a net loss (negative net income)? | mart_finance_scorecard | NULL net income |
| q006 | Which 5 metro areas have the highest median household income? | dim_geography | |
| q007 | Which metro area has the highest uninsured rate? | dim_geography | ties |
| q008 | How many DRGs are in each service line? | dim_drg | |
| q009 | How many hospitals provide emergency services? | dim_hospital | flag values |
| q010 | What is the total number of Medicare discharges across all hospitals? | fct_medicare_drg | single number |
| q011 | Which 10 hospitals have the highest net income? | mart_finance_scorecard | |
| q012 | How many hospitals are there in each ownership category? | dim_hospital | |
| q013 | Which 10 hospitals have the highest charity care cost in dollars? | mart_finance_scorecard | dollars, not percent |
| q014 | How many hospitals have a cost-to-charge ratio flagged as an outlier? | fct_hospital_finance | NULL vs false |
| q015 | Which 5 metro areas have the largest population? | dim_geography | |

## Medium (25)

| id | question | main tables | watch out for |
|---|---|---|---|
| q016 | What is the average net income by hospital ownership category? | mart_finance_scorecard | |
| q017 | How many total Medicare discharges does each service line have? | fct_medicare_drg, dim_drg | |
| q018 | Which 10 DRGs have the most total Medicare discharges? | fct_medicare_drg, dim_drg | sum, not avg |
| q019 | What is the discharge-weighted average Medicare payment per discharge in each state? | fct_medicare_drg, dim_hospital | weighting |
| q020 | Which states have more than 50 hospitals? | dim_hospital | HAVING |
| q021 | What is the average uninsured rate of the metro areas by hospital ownership category? | mart_burden_vs_community | NULL metros |
| q022 | Which service lines have a negative average margin per discharge across all hospitals? | mart_reimbursement_gap | weighted or not: decide |
| q023 ★ | Which 10 hospitals in Georgia received the most total Medicare payments? | fct_medicare_drg, dim_hospital | total = avg x discharges |
| q024 | How many hospitals are in each rural/urban (RUCA) category? | dim_hospital | |
| q025 | What is the average star rating by ownership category? | dim_hospital | rating type, unrated |
| q026 | Which 10 metro areas have the most hospitals? | dim_hospital, dim_geography | non-metro excluded |
| q027 | How many hospitals in each state spend more than 5% of net patient revenue on charity care? | mart_finance_scorecard, dim_hospital | fraction vs percent |
| q028 ★ | Which service line received the most total Medicare payment dollars? | fct_medicare_drg, dim_drg | total = avg x discharges |
| q029 | For heart failure DRGs, which 5 states have the highest discharge-weighted average Medicare payment? | fct_medicare_drg, dim_drg, dim_hospital | DRG text filter |
| q030 | What is the average median household income of the metro areas that hospitals with over 500 beds are in? | mart_finance_scorecard, dim_geography | |
| q031 | What share of hospitals is non-profit, proprietary and government? | dim_hospital | category grouping |
| q032 | What is the average cost-to-charge ratio by ownership category, excluding outliers? | fct_hospital_finance, dim_hospital | NULL and outliers |
| q033 | Which 10 hospitals have the most Medicare discharges in the Cardiology service line? | fct_medicare_drg, dim_drg, dim_hospital | |
| q034 | What is the average number of beds per hospital in each state with at least 20 hospitals? | mart_finance_scorecard, dim_hospital | |
| q035 | How many hospitals are in metro areas with an uninsured rate above 12%, by state? | dim_hospital, dim_geography | |
| q036 | For each service line, what are total derived cost and total Medicare payment? | mart_reimbursement_gap | per-discharge vs totals |
| q037 | How many hospitals have a different Medicare payment CBSA than their physical CBSA, by state? | dim_hospital | NULL vs false |
| q038 | For each service line, what is the ratio of total charges to total Medicare payment? | mart_reimbursement_gap | ratio of sums |
| q039 | Which 10 states have the most hospitals with emergency services? | dim_hospital | |
| q040 ★ | Which ownership category spends the highest share of its total net patient revenue on uncompensated care? | mart_finance_scorecard | sum/sum, not mean of ratios |

## Hard (15)

| id | question | main tables | technique |
|---|---|---|---|
| q041 | Who are the top 3 hospitals by net income in each state? | mart_finance_scorecard, dim_hospital | row_number partition by state |
| q042 | For each service line, which hospital has the highest average margin per discharge? | mart_reimbursement_gap | ranking in groups |
| q043 | Which hospitals spend a higher share of revenue on charity care than their state average? | mart_finance_scorecard, dim_hospital | CTE + comparison |
| q044 | Split hospitals into revenue quartiles and show how many are in each, with average net income. | mart_finance_scorecard | ntile |
| q045 ★ | For each state, what is its share of national Medicare discharges, and what is the cumulative share ordering states largest first? | fct_medicare_drg, dim_hospital | running sum window |
| q046 | In each service line, which 3 DRGs have the most total Medicare payments? | fct_medicare_drg, dim_drg | rank in groups |
| q047 | Which Cardiology hospital x DRG rows are in the worst 10% by margin? | mart_reimbursement_gap | percent_rank |
| q048 | Which metro areas have median income below the median of all metros and an uninsured rate above the median, and how many hospitals does each have? | dim_geography, dim_hospital | median CTE + join |
| q049 | For each ownership category, which hospital has the highest uncompensated care share of revenue, and how does it compare to its category average? | mart_finance_scorecard | CTE + window |
| q050 ★ | Which DRGs with at least 50 hospitals have the most variation in Medicare payment across hospitals (coefficient of variation)? | fct_medicare_drg, dim_drg | stddev / mean, having |
| q051 | For DRG 291, which 10 hospitals are paid the most above the national discharge-weighted average? | fct_medicare_drg, dim_hospital | CTE benchmark |
| q052 | In each of the 10 metros with the most hospitals, which service line has the worst margin rank? | mart_service_line_margin, dim_hospital | rank filter |
| q053 | What is the correlation between a metro area's uninsured rate and its hospitals' average charity care share of revenue? | mart_burden_vs_community | corr() |
| q054 | Which hospitals are in the top quartile for both net patient revenue and charity care share? | mart_finance_scorecard | two ntile / percentiles |
| q055 | For each state, what is the single service line with the largest total Medicare payments, and what share of the state's total is it? | fct_medicare_drg, dim_drg, dim_hospital | rank + share |

## Ambiguous (5), tagged `ambiguous: true`

The agent should ask a clarifying question instead of guessing.

| id | question | why it is ambiguous |
|---|---|---|
| q056 | Which are the top hospitals? | top by what: revenue, beds, rating, margin, discharges? |
| q057 | Which state is the most expensive? | cost, charges, payments, per discharge or total? |
| q058 | Show me the biggest hospitals. | beds, revenue, discharges? |
| q059 | How much does Medicare pay? | per discharge, total, which DRG or hospital? |
| q060 | Which hospitals are doing well? | profit, rating, margin, charity? |

## Unanswerable (3), tagged `unanswerable: true`

The agent should say the warehouse cannot answer this, not invent a query.

| id | question | why |
|---|---|---|
| q061 | How have Medicare payments changed from last year to this year? | one period only |
| q062 | Which doctors at Emory have the highest readmission rates? | no physician or readmission data |
| q063 | What was the patient mix by age group at Grady in March? | no age or monthly data |

## Mix check

- Easy 15, Medium 25, Hard 15 = 55 scored questions, plus 5 ambiguous and 3 unanswerable = 63.
- Holdout ★ (6 now). Add ~4 more later for a holdout of about 10.
- Coverage: every mart is used. Not covered: `dim_date` (no useful question exists, so it is a candidate to leave off the allow-list).

## Your Level 1 work

1. Edit this list. Remove anything you would not really ask, add questions an analyst would.
2. For each question, write the gold SQL and run it in your SQL client.
3. Read the result and decide whether it really answers the question. Record decisions in `notes`.
4. Tell me when done and I will convert the file to `golden_set.jsonl` and write `build_expected.py`.
