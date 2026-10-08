#!/bin/bash
# The 23 undeclared bars across both products.
#
# Your rule: gate the bars that are contracts or safety; do not gate diagnostics,
# bands, harness latencies or costs. Each line below is that rule applied, with your
# own wording from the sitting as the --reason where you gave one. They are SUGGESTIONS
# -- read each, edit or swap the intent, then run this file. `gate` takes no reason;
# `report_only` requires one, and the database enforces that.
#
# Two worth a second look are flagged INLINE below.
set -euo pipefail
cd /Users/surabhideb/Desktop/private_operating_layer_Github
set -a; . ./.env; set +a
export LAYER_DATABASE_URL="${LAYER_DATABASE_URL%/layer}/layer_ac16"
export LAYER_ADMIN_DATABASE_URL="${LAYER_ADMIN_DATABASE_URL%/layer}/layer_ac16"
ORG=8441fd41-3dd0-4b3a-9c6a-9eed5ec6a561
ME=debsurabhi30@gmail.com
L=".venv/bin/python -m layer clauses gate"

# ===== triage =====

#  1. TRI-11.1  Overall team accuracy >= 85%
#     Already gated by gate.py; declaring it makes the intent survive a reword.
$L triage TRI-11.1 --intent gate --org "$ORG" --as "$ME"
# $L triage TRI-11.1 --intent report_only --org "$ORG" --as "$ME" \
#     --reason ""

#  2. TRI-11.2  Escalation recall >= 99%
#     Already gated. Your words: a missed escalation is the worst outcome available.
$L triage TRI-11.2 --intent gate --org "$ORG" --as "$ME"
# $L triage TRI-11.2 --intent report_only --org "$ORG" --as "$ME" \
#     --reason ""

#  3. TRI-11.3  Escalation precision >= 70%
# $L triage TRI-11.3 --intent gate --org "$ORG" --as "$ME"
$L triage TRI-11.3 --intent report_only --org "$ORG" --as "$ME" \
    --reason 'escalation precision trades against escalation recall, which is gated, so a gate here can block a legitimate recall improvement.'

#  4. TRI-11.4  Critical cases C1 to C7 = 100%
$L triage TRI-11.4 --intent gate --org "$ORG" --as "$ME"
# $L triage TRI-11.4 --intent report_only --org "$ORG" --as "$ME" \
#     --reason ""

#  5. TRI-11.5  Contract validity = 100%
#     Already gated. Your words: a hard failure, not a trend.
$L triage TRI-11.5 --intent gate --org "$ORG" --as "$ME"
# $L triage TRI-11.5 --intent report_only --org "$ORG" --as "$ME" \
#     --reason ""

#  6. TRI-11.6  needs_clarification rate, 3% to 8%
# $L triage TRI-11.6 --intent gate --org "$ORG" --as "$ME"
$L triage TRI-11.6 --intent report_only --org "$ORG" --as "$ME" \
    --reason 'a 3 to 8 percent band is a health indicator rather than a pass or fail gate, and failing a build on within-band movement is noise.'

#  7. TRI-12.1  p50 latency <= 1s
# $L triage TRI-12.1 --intent gate --org "$ORG" --as "$ME"
$L triage TRI-12.1 --intent report_only --org "$ORG" --as "$ME" \
    --reason 'p50 latency in an eval harness measures the test machine rather than the product, so a gate would fail for reasons unrelated to quality.'

#  8. TRI-12.2  p95 latency <= 3s
# $L triage TRI-12.2 --intent gate --org "$ORG" --as "$ME"
$L triage TRI-12.2 --intent report_only --org "$ORG" --as "$ME" \
    --reason 'p95 latency in a harness is not the production latency, so a gate on it is not evidence about the product.'

#  9. TRI-12.3  Cost per message <= 0.01
# $L triage TRI-12.3 --intent gate --org "$ORG" --as "$ME"
$L triage TRI-12.3 --intent report_only --org "$ORG" --as "$ME" \
    --reason 'cost per message in an eval run reflects whichever model was tested rather than a stated promise.'

# 10. TRI-12.4  Cost per month <= 1200
# $L triage TRI-12.4 --intent gate --org "$ORG" --as "$ME"
$L triage TRI-12.4 --intent report_only --org "$ORG" --as "$ME" \
    --reason 'cost per month cannot be observed by a single CI run at all, so no gate can check it; this belongs in budget monitoring.'


# ===== policydesk =====

# 11. PD-5.7  Below 15% it ... (read the clause; it parsed oddly)
#     UNDECIDED: the clause label parsed as 'Below 15% it', so read the spec line
#     before declaring anything about it. Both lines left commented.
# $L policydesk PD-5.7 --intent gate --org "$ORG" --as "$ME"
# $L policydesk PD-5.7 --intent report_only --org "$ORG" --as "$ME" \
#     --reason ""

# 12. PD-8.1  recall@5 >= 95%
$L policydesk PD-8.1 --intent gate --org "$ORG" --as "$ME"
# $L policydesk PD-8.1 --intent report_only --org "$ORG" --as "$ME" \
#     --reason ""

# 13. PD-8.2  recall@1 >= 80%
#     NOTE: recall@1 reads as a diagnostic ('what a reranker moves'), but the gate
#     already enforces it at 0.80, so suggesting gate follows the existing check.
$L policydesk PD-8.2 --intent gate --org "$ORG" --as "$ME"
# $L policydesk PD-8.2 --intent report_only --org "$ORG" --as "$ME" \
#     --reason ""

# 14. PD-8.3  MRR >= 0.85
# $L policydesk PD-8.3 --intent gate --org "$ORG" --as "$ME"
$L policydesk PD-8.3 --intent report_only --org "$ORG" --as "$ME" \
    --reason 'mean reciprocal rank is one the metric engine declines to compute, and it moves with recall@5 which is gated already, so this is a redundant check nothing verifies.'

# 15. PD-8.4  Citation validity = 100%
$L policydesk PD-8.4 --intent gate --org "$ORG" --as "$ME"
# $L policydesk PD-8.4 --intent report_only --org "$ORG" --as "$ME" \
#     --reason ""

# 16. PD-8.5  Groundedness >= 98%
$L policydesk PD-8.5 --intent gate --org "$ORG" --as "$ME"
# $L policydesk PD-8.5 --intent report_only --org "$ORG" --as "$ME" \
#     --reason ""

# 17. PD-8.6  Abstention recall >= 95%
$L policydesk PD-8.6 --intent gate --org "$ORG" --as "$ME"
# $L policydesk PD-8.6 --intent report_only --org "$ORG" --as "$ME" \
#     --reason ""

# 18. PD-8.7  Abstention precision >= 70%
# $L policydesk PD-8.7 --intent gate --org "$ORG" --as "$ME"
$L policydesk PD-8.7 --intent report_only --org "$ORG" --as "$ME" \
    --reason 'abstention precision trades against abstention recall, which is gated, so failing builds on both blocks the trade-off the product needs.'

# 19. PD-8.8  Critical C1 to C6 = 100%
$L policydesk PD-8.8 --intent gate --org "$ORG" --as "$ME"
# $L policydesk PD-8.8 --intent report_only --org "$ORG" --as "$ME" \
#     --reason ""

# 20. PD-8.9  Contract validity = 100%
$L policydesk PD-8.9 --intent gate --org "$ORG" --as "$ME"
# $L policydesk PD-8.9 --intent report_only --org "$ORG" --as "$ME" \
#     --reason ""

# 21. PD-9.1  p50 latency <= 3s
# $L policydesk PD-9.1 --intent gate --org "$ORG" --as "$ME"
$L policydesk PD-9.1 --intent report_only --org "$ORG" --as "$ME" \
    --reason 'p50 latency in an eval harness measures the test machine rather than the product.'

# 22. PD-9.2  p95 latency <= 6s
#     NOTE: your rule says harness latency is not product evidence, but the gate
#     already checks p95 < 6.0. report_only is still coherent -- the Layer will not
#     propose ADDING a gate, and the existing one keeps running.
# $L policydesk PD-9.2 --intent gate --org "$ORG" --as "$ME"
$L policydesk PD-9.2 --intent report_only --org "$ORG" --as "$ME" \
    --reason 'p95 latency in a harness is not the production latency, so a gate on it is not evidence about the product. This deliberately contradicts the live gate, which checks p95 under 6s, so that the contradiction is visible and somebody goes and unblocks it.'

# 23. PD-9.3  Cost per question <= 0.03
# $L policydesk PD-9.3 --intent gate --org "$ORG" --as "$ME"
$L policydesk PD-9.3 --intent report_only --org "$ORG" --as "$ME" \
    --reason 'cost per question in an eval run reflects whichever model was tested rather than a stated promise.'


# What is left undeclared afterwards, which the generator will decline on.
for p in triage policydesk; do
  echo "--- $p ---"
  .venv/bin/python -m layer clauses list $p --org "$ORG" --as "$ME"
done
