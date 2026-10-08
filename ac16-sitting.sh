#!/bin/bash
# AC-16: the sitting. 21 proposals, one decision each.
#
# Uncomment exactly one line per proposal. Neither is pre-chosen on purpose: a
# 19-of-21 accept scores above B6's 50% to 85% band, which the specification reads
# as a rubber stamp and therefore a failure. A reject needs a reason, because
# rejecting is a decision and not a deferral.
#
# The sitting has its own database. tests/conftest.py truncates `org CASCADE` after
# every test, so running pytest against the database in .env would wipe this queue.
set -euo pipefail
cd /Users/surabhideb/Desktop/private_operating_layer_Github

# Credentials stay in .env, which is gitignored. Only the database name differs.
set -a; . ./.env; set +a
export LAYER_DATABASE_URL="${LAYER_DATABASE_URL%/layer}/layer_ac16"
export LAYER_ADMIN_DATABASE_URL="${LAYER_ADMIN_DATABASE_URL%/layer}/layer_ac16"
ORG=8441fd41-3dd0-4b3a-9c6a-9eed5ec6a561
ME=debsurabhi30@gmail.com
P=".venv/bin/python -m layer proposals"

#  1. [ci_change] file:products/triage/gate.py.scope
#     latest_only  ->  all_runs
#     products/triage/gate.py checks contract_validity at None, escalation_recall at None,
#     team_accuracy at 0.85 against latest only, so a run that missed the bar earlier in the
#     sequence never fails a build. TRI-11.1; TRI-11.2; TRI-11.5 state bars it is meant to hold.
$P accept 3fc33b66-937e-48aa-aa23-44768ab67790 --org "$ORG" --as "$ME"
# $P reject 3fc33b66-937e-48aa-aa23-44768ab67790 --org "$ORG" --as "$ME" --reason ""

#  2. [ticket] clause:TRI-11.1.ticket
#     —  ->  TRI-11.1 is below its stated bar
#     TRI-11.1 states >= 85% and is missed in 13 of 47 runs, worst 0.5 in run 20260915-133115Z-v2,
#     first in run 20260915-131327Z-v1. The history clears the bar in other runs, so the bar is
#     reachable.
$P accept 0bdfad8d-8d1d-4725-97d3-09a37a51e567 --org "$ORG" --as "$ME"
# $P reject 0bdfad8d-8d1d-4725-97d3-09a37a51e567 --org "$ORG" --as "$ME" --reason ""

#  3. [ticket] clause:TRI-11.2.ticket
#     —  ->  TRI-11.2 is below its stated bar
#     TRI-11.2 states >= 99% and is missed in 7 of 47 runs, worst 0.8 in run 20260915-133223Z-v2,
#     first in run 20260915-133223Z-v2. The history clears the bar in other runs, so the bar is
#     reachable.
$P accept 863de9e8-a4da-4a4a-a528-06f0c7539b9a --org "$ORG" --as "$ME"
# $P reject 863de9e8-a4da-4a4a-a528-06f0c7539b9a --org "$ORG" --as "$ME" --reason ""

#  4. [ci_change] clause:TRI-11.3.enforced
#     false  ->  true
#     TRI-11.3 states >= 70% and no file in the code source checks escalation_precision: 1 CI
#     file(s) were scanned.
# $P accept 809238ad-136b-4585-83cc-3a866c5cd145 --org "$ORG" --as "$ME"
$P reject 809238ad-136b-4585-83cc-3a866c5cd145 --org "$ORG" --as "$ME" --reason 'escalation_precision trades against escalation_recall, so a gate here can block a legitimate recall improvement; report it rather than failing builds on it.'

#  5. [ci_change] clause:TRI-11.4.enforced
#     false  ->  true
#     TRI-11.4 states >= 100% and no file in the code source checks critical_cases_c1_to_c7: 1 CI
#     file(s) were scanned.
$P accept 5e64a416-5eaa-4392-81b2-8f1f9752fea4 --org "$ORG" --as "$ME"
# $P reject 5e64a416-5eaa-4392-81b2-8f1f9752fea4 --org "$ORG" --as "$ME" --reason ""

#  6. [ticket] clause:TRI-11.5.ticket
#     —  ->  TRI-11.5 is below its stated bar
#     TRI-11.5 states >= 100% and is missed in 7 of 47 runs, worst 0.9286 in run
#     20260915-133223Z-v2, first in run 20260915-133223Z-v2. The history clears the bar in other
#     runs, so the bar is reachable.
$P accept 5aea450b-294a-4520-995a-146da2c63cee --org "$ORG" --as "$ME"
# $P reject 5aea450b-294a-4520-995a-146da2c63cee --org "$ORG" --as "$ME" --reason ""

#  7. [ci_change] clause:TRI-11.6.enforced
#     false  ->  true
#     TRI-11.6 states between 0.03 and 0.08 and no file in the code source checks
#     needs_clarification_rate: 1 CI file(s) were scanned.
# $P accept 807ecb01-514b-468a-a80e-dab213a10d2b --org "$ORG" --as "$ME"
$P reject 807ecb01-514b-468a-a80e-dab213a10d2b --org "$ORG" --as "$ME" --reason 'a 3 to 8 percent band is a health indicator rather than a pass or fail gate, and failing a build on within-band movement is noise.'

#  8. [ci_change] clause:TRI-12.1.enforced
#     false  ->  true
#     TRI-12.1 states <= 1 and no file in the code source checks p50_latency: 1 CI file(s) were
#     scanned.
# $P accept b75a164c-8eaf-4346-94ae-55708e9693a5 --org "$ORG" --as "$ME"
$P reject b75a164c-8eaf-4346-94ae-55708e9693a5 --org "$ORG" --as "$ME" --reason 'p50 latency in an eval harness measures the test machine rather than the product, so this gate would fail for reasons unrelated to quality.'

#  9. [ci_change] clause:TRI-12.2.enforced
#     false  ->  true
#     TRI-12.2 states <= 3 and no file in the code source checks p95_latency: 1 CI file(s) were
#     scanned.
# $P accept 122f0bd1-e50a-41e6-85ea-895cf0621cb0 --org "$ORG" --as "$ME"
$P reject 122f0bd1-e50a-41e6-85ea-895cf0621cb0 --org "$ORG" --as "$ME" --reason 'p95 latency in a harness is not the production latency, so a gate on it is not evidence about the product.'

# 10. [ci_change] clause:TRI-12.3.enforced
#     false  ->  true
#     TRI-12.3 states <= 0.01 and no file in the code source checks cost_per_message: 1 CI file(s)
#     were scanned.
# $P accept d954ea55-0db5-43a0-b155-cce3085bd4bd --org "$ORG" --as "$ME"
$P reject d954ea55-0db5-43a0-b155-cce3085bd4bd --org "$ORG" --as "$ME" --reason 'cost per message in an eval run reflects whichever model was tested rather than a stated promise.'

# 11. [ci_change] clause:TRI-12.4.enforced
#     false  ->  true
#     TRI-12.4 states <= 1200 and no file in the code source checks cost_per_month: 1 CI file(s)
#     were scanned.
# $P accept 8d2ecbc7-0f3e-454d-aa82-7a7c0f49c467 --org "$ORG" --as "$ME"
$P reject 8d2ecbc7-0f3e-454d-aa82-7a7c0f49c467 --org "$ORG" --as "$ME" --reason 'cost per month cannot be observed by a single CI run at all, so no gate can check it; this belongs in budget monitoring.'

# 12. [ci_change] clause:PD-8.3.enforced
#     false  ->  true
#     PD-8.3 states >= 85% and no file in the code source checks mrr: 1 CI file(s) were scanned.
# $P accept ddc25a50-06d7-41d3-bdfa-3bcfa8bc5791 --org "$ORG" --as "$ME"
$P reject ddc25a50-06d7-41d3-bdfa-3bcfa8bc5791 --org "$ORG" --as "$ME" --reason 'mean reciprocal rank is one the metric engine declines to compute, and it moves with recall@5 which is gated already, so this is a redundant check nothing verifies.'

# 13. [ci_change] clause:PD-8.5.enforced
#     false  ->  true
#     PD-8.5 states >= 98% and no file in the code source checks groundedness: 1 CI file(s) were
#     scanned.
$P accept 0350b597-6006-4432-8863-0bb388b40190 --org "$ORG" --as "$ME"
# $P reject 0350b597-6006-4432-8863-0bb388b40190 --org "$ORG" --as "$ME" --reason ""

# 14. [ci_change] clause:PD-8.6.enforced
#     false  ->  true
#     PD-8.6 states >= 95% and products/policydesk/tests/test_eval.py#L109 contains a check for
#     abstention_recall, but the check is skipped or expected to fail, so this check cannot fail a
#     build.
$P accept 73aedfec-8c07-4759-819a-4bc761687fe8 --org "$ORG" --as "$ME"
# $P reject 73aedfec-8c07-4759-819a-4bc761687fe8 --org "$ORG" --as "$ME" --reason ""

# 15. [ci_change] clause:PD-8.7.enforced
#     false  ->  true
#     PD-8.7 states >= 70% and no file in the code source checks abstention_precision: 1 CI
#     file(s) were scanned.
# $P accept 41dfd851-5bbe-4d99-a3f8-b65c75c067c0 --org "$ORG" --as "$ME"
$P reject 41dfd851-5bbe-4d99-a3f8-b65c75c067c0 --org "$ORG" --as "$ME" --reason 'abstention precision trades against abstention recall, which is gated, so failing builds on both blocks the trade-off the product needs.'

# 16. [ci_change] clause:PD-8.8.enforced
#     false  ->  true
#     PD-8.8 states >= 100% and no file in the code source checks critical_pass_rate: 1 CI file(s)
#     were scanned.
$P accept 1541f480-5884-4d1a-9111-96f94e7a5e60 --org "$ORG" --as "$ME"
# $P reject 1541f480-5884-4d1a-9111-96f94e7a5e60 --org "$ORG" --as "$ME" --reason ""

# 17. [clause_change] clause:PD-8.8.value
#     1.0  ->  0.4545
#     PD-8.8 states >= 100% and no run on record has reached it: 7 of 7 runs are below, the
#     furthest at 0.4545 in run 20260917-071445Z-v1. The clause is measured and not ratified, so
#     the bar was stated rather than agreed against the record.
# $P accept 64a44d61-cf1e-456a-98c4-70aa772b6a5b --org "$ORG" --as "$ME"
$P reject 64a44d61-cf1e-456a-98c4-70aa772b6a5b --org "$ORG" --as "$ME" --reason 'lowering a no-tolerance critical bar to the worst observed run makes the clause decorative, and the product reached 0.909 so the floor is the wrong end of the range; if the bar is genuinely unreachable that is a product decision rather than a rewrite.'

# 18. [ci_change] clause:PD-9.1.enforced
#     false  ->  true
#     PD-9.1 states <= 3 and no file in the code source checks p50_latency: 1 CI file(s) were
#     scanned.
# $P accept 9702873d-37a1-450a-93e1-8462e693ac0f --org "$ORG" --as "$ME"
$P reject 9702873d-37a1-450a-93e1-8462e693ac0f --org "$ORG" --as "$ME" --reason 'p50 latency in an eval harness measures the test machine rather than the product.'

# 19. [ci_change] clause:PD-9.3.enforced
#     false  ->  true
#     PD-9.3 states <= 0.03 and no file in the code source checks cost_per_question: 1 CI file(s)
#     were scanned.
# $P accept 70664c37-a03d-434e-9ec4-6a24acdbfb2d --org "$ORG" --as "$ME"
$P reject 70664c37-a03d-434e-9ec4-6a24acdbfb2d --org "$ORG" --as "$ME" --reason 'cost per question in an eval run reflects whichever model was tested rather than a stated promise.'

# 20. [new_clause] clause:PD-NEW-STATUS-OK.statement
#     —  ->  status_ok is measured and no clause states a bar for it
#     status_ok is measured across 7 run(s), latest 0.94, and no clause states a bar for it. H16:
#     the gap is the absent clause rather than the measurement.
$P accept 657a0a36-dc79-4dfb-969c-b41e75020a42 --org "$ORG" --as "$ME"
# $P reject 657a0a36-dc79-4dfb-969c-b41e75020a42 --org "$ORG" --as "$ME" --reason ""

# 21. [ci_change] clause:PD-8.1.enforced
#     false  ->  true
#     PD-8.1 states >= 95% and no file in the code source checks recall_5: 1 CI file(s) were
#     scanned.
$P accept 458ca9ce-20e0-4298-8ce1-b3feea67ac44 --org "$ORG" --as "$ME"
# $P reject 458ca9ce-20e0-4298-8ce1-b3feea67ac44 --org "$ORG" --as "$ME" --reason ""

# The one number the product is judged by. Report it wherever it lands (EC-5).
$P acceptance --org "$ORG" --as "$ME"
