-- Rollback: 20260812041301_audit_chain_genesis_marker
-- CUI // SP-CTI
--
-- Dropping the marker does not un-chain anything: the hashes already written
-- into audit_trail stay valid and keep verifying. What is lost is the ability
-- to tell a pre-cutover row from a broken one, so a rollback puts the verifier
-- back to reporting every legacy row as simply unverified.

DROP TABLE IF EXISTS audit_chain_genesis;
