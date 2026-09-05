# CUI // SP-CTI
#
# floci IaC gate fixture -- THE VIOLATING ONE (flx-ci-01).
#
# Declared expectation in args/floci_iac_gate.yaml:
#     expect_gate: fail      the preapply gate must catch it
#     expect_api: accepted   floci's AWS API surface builds it ANYWAY
#
# THAT DISAGREEMENT IS THE POINT AND IT IS NOT A DEFECT. An untagged bucket is
# a governance violation, not an API error: AWS accepts it, floci accepts it,
# and the gate is the only thing between it and the estate. The driver
# classifies this cell `gate_stricter_than_api` and it can NEVER be a finding.
# Only the opposite cell -- the gate passing something the API surface refuses
# (`gate_missed_rejection`) -- fails the job.
#
# WITHOUT THIS FIXTURE the job proves nothing. A run over the compliant fixture
# alone is green whether the gate discriminates or has silently stopped
# evaluating anything at all -- `_load_iqe_queries()` swallows IQESyntaxError
# and returns [], and `_run_iqe_checks` returns [] for no queries, which
# reports `pass`. This file is what makes the check's own firing measurable.
#
# It fires THREE independent checks, not one, so a single check regressing
# cannot quietly turn this fixture green:
#   untagged_resources       no tags at all -> tags column is null
#   fips_compliance_check    | preapply_gate defaults an untagged resource's
#   cross_region_data_paths  | classification to CUI (the ICDEV posture), and
#                            | a plan-time S3 bucket carries no resolvable
#                            | region and no "fips" in its config.
#
# Same two structural constraints as the compliant fixture: no `provider "aws"`
# block, and only services named in FLOCI_PROVIDER_OVERRIDE's endpoints{}.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

resource "aws_s3_bucket" "untagged" {
  bucket = "icdev-flocigate-violating"
}
