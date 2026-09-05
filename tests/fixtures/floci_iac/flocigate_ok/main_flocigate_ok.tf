# CUI // SP-CTI
#
# floci IaC gate fixture -- THE COMPLIANT ONE (flx-ci-01).
#
# Declared expectation in args/floci_iac_gate.yaml:
#     expect_gate: pass      the preapply gate finds no violation
#     expect_api: accepted   floci's AWS API surface builds it
#
# TWO CONSTRAINTS THIS FILE IS UNDER, and both fail SILENTLY if broken:
#
#  1. NO `provider "aws"` BLOCK. tools/studio/executors/_base.py injects
#     FLOCI_PROVIDER_OVERRIDE as floci_override.tf into the workspace, and a
#     second provider block for the same alias is a duplicate-configuration
#     error -- terraform refuses, which reads as the emulator rejecting the
#     plan when nothing was ever sent to it.
#
#  2. ONLY SERVICES LISTED IN THAT OVERRIDE'S endpoints{} BLOCK. The override
#     names s3, ec2, rds, neptune, elasticache, iam, ssm, sts and kms. A
#     resource of any OTHER service (sqs, sns, dynamodb, ...) is NOT redirected
#     and the provider talks to REAL AWS with the dummy credentials -- which
#     fails with an auth error that looks exactly like an emulator problem.
#     tests/ci/test_floci_iac_gate.py re-derives the allowed set from the
#     override itself and refuses a fixture that steps outside it.
#
# WHY IT PASSES THE GATE, check by check
# (context/iqe/queries/infra/*.iqe, run over the plan delta):
#   untagged_resources        tags survive with Project/Environment after
#                             preapply_gate pops Classification out of them.
#                             A resource whose ONLY tag is Classification
#                             reports tags=null and FIRES.
#   fips_compliance_check     |
#   cross_region_data_paths   | all three are scoped to classification == CUI.
#   high_cost_cui_resources   | This fixture is UNCLASSIFIED -- a CI fixture
#                             holds no controlled data, and saying so is more
#                             honest than tagging it CUI and then arranging a
#                             GovCloud region and a "fips" substring to get a
#                             pass out of checks that were never satisfied.
#   expired_certs             config carries no "cert_expired".

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 5.x, deliberately: FLOCI_PROVIDER_OVERRIDE emits `s3_use_path_style`,
      # which is the v5 spelling (v4 called it s3_force_path_style), and v6
      # moved `region` onto individual resources. Pinning the major keeps the
      # plan JSON the gate parses stable.
      version = "~> 5.0"
    }
  }
}

resource "aws_s3_bucket" "compliant" {
  bucket = "icdev-flocigate-ok"

  tags = {
    Classification = "UNCLASSIFIED"
    Project        = "icdev-floci-iac-gate"
    Environment    = "ci"
  }
}
