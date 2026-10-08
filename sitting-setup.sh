#!/bin/bash
# Set up a sitting from nothing: a database of its own, both reference products
# onboarded through the CLI, the bindings confirmed, the gate intents declared, and the
# proposals generated. Ends by printing the queue size and the (unmeasured) rate.
#
#   ./sitting-setup.sh layer_ac16_round2
#
# It does NOT decide anything. Deciding is a human's, and the script it writes for that
# is the last thing it prints.
#
# **Why a database per sitting.** `tests/conftest.py`'s `clean_tenants` is autouse and
# TRUNCATEs `org CASCADE` after every test, so a sitting in the database named in `.env`
# is destroyed by the next `pytest` run -- which is how the first one was lost mid-way.
# And `proposals acceptance` counts every decided proposal in the org, so generating a
# second round beside a decided first one makes both numbers meaningless.
set -euo pipefail
cd /Users/surabhideb/Desktop/private_operating_layer_Github

DB="${1:-}"
if [[ -z "$DB" ]]; then
    echo "usage: $0 <database-name>   e.g. $0 layer_ac16_round2" >&2
    exit 2
fi
ME="${LAYER_SITTING_ACTOR:-debsurabhi30@gmail.com}"
REF="${LAYER_FIXTURE_REPO:-/Users/surabhideb/Desktop/chatbot-lab}"
URL="https://github.com/SurabhiDeb/chatbot-lab"

if [[ ! -d "$REF" ]]; then
    echo "the fixture repository is not at $REF. It is read-only test data, pinned at" >&2
    echo "ef07ac9, and never a dependency -- set LAYER_FIXTURE_REPO if it moved." >&2
    exit 1
fi

# Credentials stay in .env, which is gitignored. Only the database name differs.
set -a; . ./.env; set +a
export LAYER_DATABASE_URL="${LAYER_DATABASE_URL%/layer}/$DB"
export LAYER_ADMIN_DATABASE_URL="${LAYER_ADMIN_DATABASE_URL%/layer}/$DB"
L=".venv/bin/python -m layer"

echo "==> database $DB"
psql -d postgres -c "CREATE DATABASE $DB OWNER layer_owner" >/dev/null
psql -d "$DB" -c 'CREATE EXTENSION IF NOT EXISTS vector' >/dev/null
.venv/bin/alembic upgrade head 2>&1 | tail -1

ORG=$($L org create "$DB" | awk '{print $2}')
echo "==> org $ORG"
$L actor add "$ME" --role pm --org "$ORG" --as "$ME" | tail -1

# --- the product-specific shapes. Config, which is data: every one of these is a thing
# --- the product's owner states once, and none of it is knowledge the Layer carries.
onboard () {
    local key="$1" prefix="$2" spec_cfg="$3" eval_cfg="$4" code_cfg="$5"
    # Not `${key^}`: macOS ships bash 3.2, which has no case-modification expansion and
    # fails with "bad substitution" rather than ignoring it.
    local name
    name="$(printf '%s' "$key" | tr '[:lower:]' '[:upper:]' | cut -c1)$(printf '%s' "$key" | cut -c2-)"
    echo "==> $key"
    $L product register "$key" --name "$name" --ref-prefix "$prefix" \
        --org "$ORG" --as "$ME" | tail -1
    $L source bind "$key" --role spec --kind repo --org "$ORG" --as "$ME" \
        --config "$spec_cfg" | head -1
    $L source bind "$key" --role eval --kind repo --org "$ORG" --as "$ME" \
        --config "$eval_cfg" | head -1
    $L source bind "$key" --role code --kind repo --org "$ORG" --as "$ME" \
        --config "$code_cfg" | head -1
    $L spec "$key" --org "$ORG" --as "$ME" | head -1
    $L backfill "$key" --org "$ORG" --as "$ME" | head -1
}

onboard triage TRI \
 "{\"local_path\":\"$REF\",\"repo_url\":\"$URL\",\"path\":\"products/triage/SPEC.md\"}" \
 "{
   \"local_path\":\"$REF\",\"repo_url\":\"$URL\",
   \"globs\":[\"products/triage/runs/*.json\"],
   \"readers\":[{\"glob\":\"products/triage/runs/*.json\",
     \"measured_at\":\"/meta/timestamp_utc\",\"corpus_sha\":\"/meta/dataset_sha\",
     \"prompt_version\":\"/meta/prompt_sha\",\"code_rev\":\"/meta/git_sha\",
     \"cases\":{\"input_field\":\"message\"},
     \"metrics\":[
       {\"metric\":\"team_accuracy\",\"kind\":\"accuracy\",\"actual\":\"team\",\"expected\":\"labelled_team\"},
       {\"metric\":\"escalation_recall\",\"kind\":\"recall\",\"actual\":\"escalate\",\"expected\":\"labelled_escalate\",\"coerce\":{\"labelled_escalate\":\"bool\"}},
       {\"metric\":\"contract_validity\",\"kind\":\"rate\",\"where\":{\"field\":\"problem\",\"op\":\"is_blank\"}}]}]}" \
 "{
   \"local_path\":\"$REF\",\"repo_url\":\"$URL\",
   \"files\":[\"products/triage/gate.py\",\".github/workflows/ci.yml\"],
   \"metric_aliases\":{\"escalation_recall\":[\"missed\"],\"contract_validity\":[\"broken\",\"problem\"]}}"

onboard policydesk PD \
 "{\"local_path\":\"$REF\",\"repo_url\":\"$URL\",\"path\":\"products/policydesk/SPEC.md\"}" \
 "{
   \"local_path\":\"$REF\",\"repo_url\":\"$URL\",
   \"globs\":[\"products/policydesk/runs/*.json\"],
   \"readers\":[{\"glob\":\"products/policydesk/runs/*.json\",
     \"measured_at\":\"/meta/started\",\"corpus_sha\":\"/meta/corpus_sha\",
     \"prompt_version\":\"/meta/prompt_sha\",\"code_rev\":\"/meta/git\",
     \"cases\":{\"input_field\":\"question\"},
     \"metrics\":[
       {\"metric\":\"status_ok\",\"kind\":\"accuracy\",\"actual\":\"status\",\"expected\":\"expected_status\"},
       {\"metric\":\"critical_pass_rate\",\"kind\":\"accuracy\",\"actual\":\"status\",\"expected\":\"expected_status\",\"filter\":{\"field\":\"critical\",\"op\":\"is_present\"}}]}]}" \
 "{
   \"local_path\":\"$REF\",\"repo_url\":\"$URL\",
   \"files\":[\"products/policydesk/tests/test_eval.py\",\".github/workflows/ci.yml\"],
   \"metric_aliases\":{
     \"citation_validity\":[\"verbatim\",\"quotes\"],
     \"contract_validity\":[\"parsed\",\"unparseable\",\"broken\"]}}"

# --- the bindings. Human-only; these are the ones the operator confirmed on 8 Oct 2026,
# --- three by exact metric name and one paired by hand because the spec names it
# --- differently. Re-confirming a past decision is not the same as making a new one:
# --- read them before running this against a product whose spec has moved.
echo "==> bindings"
$L bindings confirm triage --metric contract_validity --clause TRI-11.5 --org "$ORG" --as "$ME" | head -1
$L bindings confirm triage --metric escalation_recall  --clause TRI-11.2 --org "$ORG" --as "$ME" | head -1
$L bindings confirm triage --metric team_accuracy      --clause TRI-11.1 --org "$ORG" --as "$ME" | head -1
$L bindings confirm policydesk --metric critical_pass_rate --clause PD-8.8 --org "$ORG" --as "$ME" \
    --note "paired by hand: the specification names this bar differently" | head -1

echo "==> scan and measure"
for p in triage policydesk; do
    $L scan "$p" --org "$ORG" --as "$ME" | head -1
    $L measure "$p" --org "$ORG" --as "$ME" | head -1
done

# --- the gate intents, likewise human-only and likewise already decided. The rule was:
# --- gate the bars that are contracts or safety; do not gate diagnostics, bands,
# --- harness latencies or costs. PD-5.7 is left undeclared on purpose -- see PROGRESS.
echo "==> gate intents"
G="$L clauses gate"
for ref in TRI-11.1 TRI-11.2 TRI-11.4 TRI-11.5; do
    $G triage "$ref" --intent gate --org "$ORG" --as "$ME" | head -1
done
for ref in PD-8.1 PD-8.2 PD-8.4 PD-8.5 PD-8.6 PD-8.8 PD-8.9; do
    $G policydesk "$ref" --intent gate --org "$ORG" --as "$ME" | head -1
done
$G triage TRI-11.3 --intent report_only --org "$ORG" --as "$ME" --reason \
 'escalation precision trades against escalation recall, which is gated, so a gate here can block a legitimate recall improvement.' | head -1
$G triage TRI-11.6 --intent report_only --org "$ORG" --as "$ME" --reason \
 'a 3 to 8 percent band is a health indicator rather than a pass or fail gate, and failing a build on within-band movement is noise.' | head -1
$G triage TRI-12.1 --intent report_only --org "$ORG" --as "$ME" --reason \
 'p50 latency in an eval harness measures the test machine rather than the product, so a gate would fail for reasons unrelated to quality.' | head -1
$G triage TRI-12.2 --intent report_only --org "$ORG" --as "$ME" --reason \
 'p95 latency in a harness is not the production latency, so a gate on it is not evidence about the product.' | head -1
$G triage TRI-12.3 --intent report_only --org "$ORG" --as "$ME" --reason \
 'cost per message in an eval run reflects whichever model was tested rather than a stated promise.' | head -1
$G triage TRI-12.4 --intent report_only --org "$ORG" --as "$ME" --reason \
 'cost per month cannot be observed by a single CI run at all, so no gate can check it; this belongs in budget monitoring.' | head -1
$G policydesk PD-8.3 --intent report_only --org "$ORG" --as "$ME" --reason \
 'mean reciprocal rank is one the metric engine declines to compute, and it moves with recall@5 which is gated already, so this is a redundant check nothing verifies.' | head -1
$G policydesk PD-8.7 --intent report_only --org "$ORG" --as "$ME" --reason \
 'abstention precision trades against abstention recall, which is gated, so failing builds on both blocks the trade-off the product needs.' | head -1
$G policydesk PD-9.1 --intent report_only --org "$ORG" --as "$ME" --reason \
 'p50 latency in an eval harness measures the test machine rather than the product.' | head -1
$G policydesk PD-9.2 --intent report_only --org "$ORG" --as "$ME" --reason \
 'p95 latency in a harness is not the production latency, so a gate on it is not evidence about the product. This deliberately contradicts the live gate, which checks p95 under 6s, so that the contradiction is visible and somebody goes and unblocks it.' | head -1
$G policydesk PD-9.3 --intent report_only --org "$ORG" --as "$ME" --reason \
 'cost per question in an eval run reflects whichever model was tested rather than a stated promise.' | head -1

echo "==> generate"
for p in triage policydesk; do
    $L generate "$p" --org "$ORG" --as "$ME" | head -1
done

echo
echo "==> the queue, undecided"
$L proposals acceptance --org "$ORG" --as "$ME"
echo
echo "ORG=$ORG   DB=$DB"
echo "Read it:   $L proposals list --org $ORG --as $ME"
echo "Then decide every one. Nothing above decided anything."
